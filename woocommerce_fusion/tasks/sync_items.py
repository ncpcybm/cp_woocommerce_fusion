import json
from dataclasses import dataclass
from datetime import datetime
from typing import List, Optional, Tuple

import frappe
import erpnext
import requests


from frappe.utils.file_manager import save_file

from frappe import log
from erpnext.stock.doctype.item.item import Item
from frappe import ValidationError, _, _dict
from frappe.query_builder import Criterion

from frappe.utils import (
	cint,
	cstr,
	flt,
	formatdate,
	get_link_to_form,
	getdate,
	now_datetime,
	nowtime,
	strip,
	strip_html,
 	get_datetime, 
  	now
)


from jsonpath_ng.ext import parse

from woocommerce_fusion.exceptions import SyncDisabledError
from woocommerce_fusion.tasks.sync import SynchroniseWooCommerce
from woocommerce_fusion.woocommerce.doctype.woocommerce_product.woocommerce_product import (
	WooCommerceProduct,
)
from woocommerce_fusion.woocommerce.doctype.woocommerce_server.woocommerce_server import (
	WooCommerceServer,
)
from woocommerce_fusion.woocommerce.woocommerce_api import (
	generate_woocommerce_record_name_from_domain_and_id,
)

from erpnext.stock.doctype.stock_entry.stock_entry_utils import make_stock_entry



def run_item_sync_from_hook(doc, method):
	"""
	Intended to be triggered by a Document Controller hook from Item
	"""
	if (
		doc.doctype == "Item"
		and not doc.flags.get("created_by_sync", None)
		and len(doc.woocommerce_servers) > 0
	):
		frappe.msgprint(
			_("Background sync to WooCommerce triggered for {0} {1}").format(frappe.bold(doc.name), method),
			indicator="blue",
			alert=True,
		)
		frappe.enqueue(clear_sync_hash_and_run_item_sync, item_code=doc.name)


@frappe.whitelist()
def run_item_sync(
	item_code: Optional[str] = None,
	item: Optional[Item] = None,
	woocommerce_product_name: Optional[str] = None,
	woocommerce_product: Optional[WooCommerceProduct] = None,
	enqueue=False,
) -> Tuple[Item, WooCommerceProduct]:
	"""
	Helper function that prepares arguments for item sync
	"""
 

	# Validate inputs, at least one of the parameters should be provided
	if not any([item_code, item, woocommerce_product_name, woocommerce_product]):
		raise ValueError(
			(
				"At least one of item_code, item, woocommerce_product_name, woocommerce_product parameters required"
			)
		)


	# Get ERPNext Item and WooCommerce product if they exist
	if woocommerce_product or woocommerce_product_name:
		if not woocommerce_product:
			woocommerce_product = frappe.get_doc(
				{"doctype": "WooCommerce Product", "name": woocommerce_product_name}
			)
			woocommerce_product.load_from_db()

		# Trigger sync
		sync = SynchroniseItem(woocommerce_product=woocommerce_product)
		if enqueue:
			frappe.enqueue(sync.run)
		else:
			sync.run()

	elif item or item_code:
		if not item:
			item = frappe.get_doc("Item", item_code)
		if not item.woocommerce_servers:
			frappe.throw(_("No WooCommerce Servers defined for Item {0}").format(item_code))
		for wc_server in item.woocommerce_servers:
			# Trigger sync for every linked server
			sync = SynchroniseItem(
				item=ERPNextItemToSync(item=item, item_woocommerce_server_idx=wc_server.idx)
			)
			if enqueue:
				frappe.enqueue(sync.run)
			else:
				sync.run()

	frappe.log_error("sync.item.item", sync.item.item)

	return (
		sync.item.item if sync and sync.item else None,
		sync.woocommerce_product if sync else None,
	)


def sync_woocommerce_products_modified_since(date_time_from=None):
	"""
	Get list of WooCommerce products modified since date_time_from
	"""
	wc_settings = frappe.get_doc("WooCommerce Integration Settings")

	if not date_time_from:
		date_time_from = wc_settings.wc_last_sync_date_items

	# Validate
	if not date_time_from:
		error_text = _(
			"'Last Items Syncronisation Date' field on 'WooCommerce Integration Settings' is missing"
		)
		frappe.log_error(
			"WooCommerce Items Sync Task Error",
			error_text,
		)
		raise ValueError(error_text)

	wc_products = get_list_of_wc_products(date_time_from=date_time_from)
	for wc_product in wc_products:
		try:
			run_item_sync(woocommerce_product=wc_product, enqueue=True)
		# Skip items with errors, as these exceptions will be logged
		except frappe.ValidationError as ve:
			frappe.log_error(f"⚠️ Validation Error: {ve}")
		except frappe.PermissionError as pe:
			frappe.log_error(f"🚫 Permission Denied: {pe}")
		except Exception as e:
			error_message = frappe.get_traceback()
			frappe.log_error(f"❌ Unexpected Error: {e}")

	wc_settings.reload()
	wc_settings.wc_last_sync_date_items = now()
	wc_settings.flags.ignore_mandatory = True
	wc_settings.save()


@dataclass
class ERPNextItemToSync:
	"""Class for keeping track of an ERPNext Item and the relevant WooCommerce Server to sync to"""

	item: Item
	item_woocommerce_server_idx: int

	@property
	def item_woocommerce_server(self):
		return self.item.woocommerce_servers[self.item_woocommerce_server_idx - 1]


class SynchroniseItem(SynchroniseWooCommerce):
	"""
	Class for managing synchronisation of WooCommerce Product with ERPNext Item
	"""

	def __init__(
		self,
		servers: List[WooCommerceServer | _dict] = None,
		item: Optional[ERPNextItemToSync] = None,
		woocommerce_product: Optional[WooCommerceProduct] = None,
	) -> None:
		super().__init__(servers)
		self.item = item
		self.woocommerce_product = woocommerce_product
		self.settings = frappe.get_cached_doc("WooCommerce Integration Settings")

	def run(self):
		"""
		Run synchronisation
		"""
		try:
			self.get_corresponding_item_or_product()
			self.sync_wc_product_with_erpnext_item()
		except Exception as err:
			try:
				woocommerce_product_dict = (
					self.woocommerce_product.as_dict()
					if isinstance(self.woocommerce_product, WooCommerceProduct)
					else self.woocommerce_product
				)
			except ValidationError as e:
				woocommerce_product_dict = self.woocommerce_product
			error_message = f"{frappe.get_traceback()}\n\nItem Data: \n{str(self.item) if self.item else ''}\n\nWC Product Data \n{str(woocommerce_product_dict) if self.woocommerce_product else ''})"
			frappe.log_error("WooCommerce Error", error_message)
			raise err

	def get_corresponding_item_or_product(self):
		"""
		If we have an ERPNext Item, get the corresponding WooCommerce Product
		If we have a WooCommerce Product, get the corresponding ERPNext Item
		"""
		if (
			self.item and not self.woocommerce_product and self.item.item_woocommerce_server.woocommerce_id
		):
			# Validate that this Item's WooCommerce Server has sync enabled
			wc_server = frappe.get_cached_doc(
				"WooCommerce Server", self.item.item_woocommerce_server.woocommerce_server
			)
			if not wc_server.enable_sync:
				raise SyncDisabledError(wc_server)

			wc_products = get_list_of_wc_products(item=self.item)
			if len(wc_products) == 0:
				raise ValueError(
					f"No WooCommerce Product found with ID {self.item.item_woocommerce_server.woocommerce_id} on {self.item.item_woocommerce_server.woocommerce_server}"
				)
			self.woocommerce_product = wc_products[0]

		if self.woocommerce_product and not self.item:
			self.get_erpnext_item()

	def get_erpnext_item(self):
		"""
		Get erpnext item for a WooCommerce Product
		"""
		if not all(
			[self.woocommerce_product.woocommerce_server, self.woocommerce_product.woocommerce_id]
		):
			raise ValueError("Both woocommerce_server and woocommerce_id required")

		iws = frappe.qb.DocType("Item WooCommerce Server")
		itm = frappe.qb.DocType("Item")

		and_conditions = [
			iws.woocommerce_server == self.woocommerce_product.woocommerce_server,
			iws.woocommerce_id == self.woocommerce_product.woocommerce_id,
		]

		item_codes = (
			frappe.qb.from_(iws)
			.join(itm)
			.on(iws.parent == itm.name)
			.where(Criterion.all(and_conditions))
			.select(iws.parent, iws.name)
			.limit(1)
		).run(as_dict=True)

		found_item = frappe.get_doc("Item", item_codes[0].parent) if item_codes else None
		if found_item:
			self.item = ERPNextItemToSync(
				item=found_item,
				item_woocommerce_server_idx=next(
					server.idx for server in found_item.woocommerce_servers if server.name == item_codes[0].name
				),
			)

	def sync_wc_product_with_erpnext_item(self):
		"""
		Syncronise Item between ERPNext and WooCommerce
		"""
		if self.item and not self.woocommerce_product:
			# create missing product in WooCommerce
			self.create_woocommerce_product(self.item)
		elif self.woocommerce_product and not self.item:
			# create missing item in ERPNext
			self.create_item(self.woocommerce_product)
		elif self.item and self.woocommerce_product:
			# both exist, check sync hash
			if (
				self.woocommerce_product.woocommerce_date_modified
				!= self.item.item_woocommerce_server.woocommerce_last_sync_hash
			):
				if get_datetime(self.woocommerce_product.woocommerce_date_modified) > get_datetime(
					self.item.item.modified
				):
					self.update_item(self.woocommerce_product, self.item)
				if get_datetime(self.woocommerce_product.woocommerce_date_modified) < get_datetime(
					self.item.item.modified
				):
					self.update_woocommerce_product(self.woocommerce_product, self.item)

	def update_item(self, woocommerce_product: WooCommerceProduct, item: ERPNextItemToSync):
		"""
		Update the ERPNext Item with fields from it's corresponding WooCommerce Product
		"""
  		

		item_dirty = False
		if item.item.item_name != woocommerce_product.woocommerce_name:
			item.item.item_name = woocommerce_product.woocommerce_name
			item_dirty = True

		fields_updated, item.item = self.set_item_fields(item=item.item)

		wc_server = frappe.get_cached_doc("WooCommerce Server", woocommerce_product.woocommerce_server)
		if wc_server.enable_image_sync:
			wc_product_images = json.loads(woocommerce_product.images)
			if len(wc_product_images) > 0:
				if item.item.image != wc_product_images[0]["src"]:
					item.item.image = wc_product_images[0]["src"]
					item_dirty = True
     
				if wc_server.sync_product_gallery:
					frappe.log_error(f"🖼️ Update Image Gallery ...")
					# clear all 
					# attached the new images
      
		if item_dirty or fields_updated:
			item.item.flags.created_by_sync = True
			item.item.save()

		self.set_sync_hash()

	def update_woocommerce_product(
		self, wc_product: WooCommerceProduct, item: ERPNextItemToSync
	) -> None:
		"""
		Update the WooCommerce Product with fields from it's corresponding ERPNext Item
		"""
		wc_product_dirty = False

		# Update properties
		if wc_product.woocommerce_name != item.item.item_name:
			wc_product.woocommerce_name = item.item.item_name
			wc_product_dirty = True

		product_fields_changed, wc_product = self.set_product_fields(wc_product, item)
		if product_fields_changed:
			wc_product_dirty = True

		if wc_product_dirty:
			wc_product.save()

		self.woocommerce_product = wc_product
		self.set_sync_hash()

	def create_woocommerce_product(self, item: ERPNextItemToSync) -> None:
		"""
		Create the WooCommerce Product with fields from it's corresponding ERPNext Item
		"""
  

		if (
			item.item_woocommerce_server.woocommerce_server
			and item.item_woocommerce_server.enabled
			and not item.item_woocommerce_server.woocommerce_id
		):
			# Create a new WooCommerce Product doc
			wc_product = frappe.get_doc({"doctype": "WooCommerce Product"})

			wc_product.type = "simple"

			# Handle variants
			if item.item.has_variants:
				wc_product.type = "variable"
				wc_product_attributes = []

				# Handle attributes
				for row in item.item.attributes:
					item_attribute = frappe.get_doc("Item Attribute", row.attribute)
					wc_product_attributes.append(
						{
							"name": row.attribute,
							"slug": row.attribute.lower().replace(" ", "_"),
							"visible": True,
							"variation": True,
							"options": [option.attribute_value for option in item_attribute.item_attribute_values],
						}
					)

				wc_product.attributes = json.dumps(wc_product_attributes)

			if item.item.variant_of:
				# Check if parent exists
				parent_item = frappe.get_doc("Item", item.item.variant_of)
				parent_item, parent_wc_product = run_item_sync(item_code=parent_item.item_code)
				wc_product.parent_id = parent_wc_product.woocommerce_id
				wc_product.type = "variation"

				# Handle attributes
				wc_product_attributes = [
					{
						"name": row.attribute,
						"slug": row.attribute.lower().replace(" ", "_"),
						"option": row.attribute_value,
					}
					for row in item.item.attributes
				]

				wc_product.attributes = json.dumps(wc_product_attributes)

			# Set properties
			wc_product.woocommerce_server = item.item_woocommerce_server.woocommerce_server
			wc_product.woocommerce_name = item.item.item_name
			wc_product.regular_price = get_item_price_rate(item) or "0"

			self.set_product_fields(wc_product, item)

			wc_product.insert()
			self.woocommerce_product = wc_product

			# Reload ERPNext Item
			item.item.reload()
			item.item_woocommerce_server.woocommerce_id = wc_product.woocommerce_id
			item.item.flags.created_by_sync = True
			item.item.save()

			self.set_sync_hash()


	def create_item_stock_entry(self,purpose, item_code, qty):
		"""
		Creates a Stock Entry with a single item.
		
		:param purpose: The purpose of the stock entry (e.g., 'Stock Transfer', 'Material Issue')
		:param item_code: The item code of the stock item
		:param qty: The quantity of the item
		:return: The name of the created Stock Entry document or None
		"""
    
		# stock_entry = frappe.new_doc("Stock Entry")
		if (
			len(
				frappe.get_list(
					"WooCommerce Server", filters={"enable_sync": 1, "enable_stock_level_synchronisation": 1}
				)
			)
			> 0
		):
			wc_server = frappe.get_cached_doc("WooCommerce Server", 'staging1.cpherbalist.com')
			if (len(wc_server.warehouses) >= 1):
				# frappe.log_error("stock_entry warehouse ...", wc_server.warehouses[0].warehouse)
				try:
					stock_entry = frappe.new_doc("Stock Entry")

					stock_entry.purpose = "Material Receipt"  # or "Material Receipt", "Material Issue", etc.
					stock_entry.stock_entry_type = "Material Receipt"
					
					stock_entry.company = "CHRYSTALLENA POULLI HERBAL SKIN CARE PRODUCTS LTD"

					# Add an item
					stock_entry.append("items", {
						"item_code": item_code,
						"qty": qty,
						"s_warehouse": wc_server.warehouses[0].warehouse,
						"t_warehouse": wc_server.warehouses[0].warehouse
					})
		
					stock_entry.add_comment("Comment", _("Opening Stock from WooCommerce"))
				
					# Save and submit
					stock_entry.save()
					stock_entry.submit()
			
					frappe.log_error("✅ stock_entry " + stock_entry.name + "...", stock_entry)
					return stock_entry.name
     
				except frappe.ValidationError as ve:
					frappe.log_error(f"⚠️ (468) Validation Error", ve)
				except frappe.PermissionError as pe:
					frappe.log_error(f"🚫 (470) Permission Denied",pe)
				except Exception as e:
					frappe.log_error(f"❌ (472) Unexpected Error",e)
     
		return None

	def create_item(self, wc_product: WooCommerceProduct) -> None:
		"""
		Create an ERPNext Item from the given WooCommerce Product
		"""
		wc_server = frappe.get_cached_doc("WooCommerce Server", wc_product.woocommerce_server)

		# Create Item
		item = frappe.new_doc("Item")

		# Handle variants' attributes
		if wc_product.type in ["variable", "variation"]:
			self.create_or_update_item_attributes(wc_product)
			wc_attributes = json.loads(wc_product.attributes)
			for wc_attribute in wc_attributes:
				row = item.append("attributes")
				row.attribute = wc_attribute["name"]
				if wc_product.type == "variation":
					row.attribute_value = wc_attribute["option"]

		# Handle variants
		if wc_product.type == "variable":
			item.has_variants = 1

		if wc_product.type == "variation":
			# Check if parent exists
			woocommerce_product_name = generate_woocommerce_record_name_from_domain_and_id(
				wc_product.woocommerce_server, wc_product.parent_id
			)
			parent_item, parent_wc_product = run_item_sync(
				woocommerce_product_name=woocommerce_product_name
			)
			item.variant_of = parent_item.item_code

		item.item_code = (
			wc_product.sku
			if wc_server.name_by == "Product SKU" and wc_product.sku
			else str(wc_product.woocommerce_id)
		)
		item.stock_uom = wc_server.uom or _("Unit")
		item.item_group = wc_server.item_group
		item.item_name = wc_product.woocommerce_name
		row = item.append("woocommerce_servers")
		row.woocommerce_id = wc_product.woocommerce_id
		row.woocommerce_server = wc_server.name
		item.flags.ignore_mandatory = True
		item.flags.created_by_sync = True

		if wc_server.enable_image_sync:
			wc_product_images = json.loads(wc_product.images)
			if len(wc_product_images) > 0:
				item.image = wc_product_images[0]["src"]
    
				if wc_server.sync_product_gallery:
					for i, img in enumerate(wc_product_images):
						image_url = img["src"]

						try:
							response = requests.get(image_url)
							response.raise_for_status()

							file_name = f"{item.item_code}_{i}.jpg"  # or use os.path.basename(image_url)

							frappe.get_doc(
								{
									"doctype": "File",
									"attached_to_doctype": "Item",
									"attached_to_name": item.item_code,
									"folder": "Home/Attachments",
									# "file_name": file_name,
									# frappe.local.uploaded_file_url,
									"file_url": image_url, 
									"is_private": 0,
									"content": response.content,
								}
							).insert(ignore_permissions=True)
       
							frappe.db.commit() 
							frappe.log_error(title="✅ Attached Image", message=f"{file_name} attached successfully.")

							# frappe.log_error(f"✅ Attached: {file_name}")

						except Exception as e:
							frappe.log_error(f"❌ Failed to attach image {image_url}: {e}")
      
				frappe.log_error("⚠️ Image list processed", str(wc_product_images))
    
		modified, item = self.set_item_fields(item=item)

		item.flags.created_by_sync = True
  
		try:
			item.insert()
		except frappe.ValidationError as ve:
			frappe.log_error(f"⚠️ (537) Validation Error", ve)
		except frappe.PermissionError as pe:
			frappe.log_error(f"🚫 (539) Permission Denied",pe)
		except Exception as e:
			frappe.log_error(f"❌ (541) Unexpected Error",e)
			# raise e

		frappe.log_error("set_item_fields", item)
		frappe.log_error("name", item.name)
  
  
		try:
			if (
			len(
				frappe.get_list(
					"WooCommerce Server", filters={"enable_sync": 1, "enable_stock_level_synchronisation": 1}
				)
			)
			> 0
		):
				wc_server_p = frappe.get_cached_doc("WooCommerce Server", 'staging1.cpherbalist.com')
				if (len(wc_server.warehouses) >= 1):
					# frappe.log_error("OK ...", "")
					# frappe.log_error("warehouse ...", wc_server_p.warehouses[0].warehouse)
    
					purpose = "Material Receipt"  # Change this based on the purpose like 'Material Receipt'
					item_code = item.item_code  # Replace with your actual item code
					qty = item.woocommerce_qty  # Quantity of the item

					stock_entry_name = self.create_item_stock_entry(purpose, item_code, qty)

					print(f"✅ Stock Entry {stock_entry_name} created successfully.")
   
		except frappe.ValidationError as ve:
			frappe.log_error(f"⚠️ (571) Validation Error", ve)
		except frappe.PermissionError as pe:
			frappe.log_error(f"🚫 (573) Permission Denied",pe)
		except Exception as e:
			frappe.log_error(f"❌ (575) Unexpected Error",e)

		self.item = ERPNextItemToSync(
			item=item,
			item_woocommerce_server_idx=next(
				iws.idx
				for iws in item.woocommerce_servers
				if iws.woocommerce_server == wc_product.woocommerce_server
			),
		)

		self.set_sync_hash()

	def create_or_update_item_attributes(self, wc_product: WooCommerceProduct):
		"""
		Create or update an Item Attribute
		"""

		if wc_product.attributes:
			wc_attributes = json.loads(wc_product.attributes)
			for wc_attribute in wc_attributes:
				if frappe.db.exists("Item Attribute", wc_attribute["name"]):
					# Get existing Item Attribute
					item_attribute = frappe.get_doc("Item Attribute", wc_attribute["name"])
				else:
					# Create a Item Attribute
					item_attribute = frappe.get_doc(
						{"doctype": "Item Attribute", "attribute_name": wc_attribute["name"]}
					)

				# Get list of attribute options.
				# In variable WooCommerce Products, it's a list with key "options"
				# In a WooCommerce Product variant, it's a single value with key "option"
				options = (
					wc_attribute["options"] if wc_product.type == "variable" else [wc_attribute["option"]]
				)

				# If no attributes values exist, or attribute values exist already but are different, remove and update them
				if len(item_attribute.item_attribute_values) == 0 or (
					len(item_attribute.item_attribute_values) > 0
					and set(options) != set([val.attribute_value for val in item_attribute.item_attribute_values])
				):
					item_attribute.item_attribute_values = []
					for option in options:
						row = item_attribute.append("item_attribute_values")
						row.attribute_value = option
						row.abbr = option.replace(" ", "")

				item_attribute.flags.ignore_mandatory = True
				if not item_attribute.name:
					item_attribute.insert()
				else:
					item_attribute.save()

	def set_item_fields(self, item: Item) -> Tuple[bool, Item]:
		"""
		If there exist any Field Mappings on `WooCommerce Server`, attempt to synchronise their values from
		WooCommerce to ERPNext
		"""
  
  
		item_dirty = False
		if item and self.woocommerce_product:
      
			if (
				len(
					frappe.get_list(
						"WooCommerce Server", filters={"enable_sync": 1, "enable_stock_level_synchronisation": 1}
					)
				)
				> 0
			):
				wc_server = frappe.get_cached_doc("WooCommerce Server", 'staging1.cpherbalist.com')
				if (len(wc_server.warehouses) >= 1):
					# frappe.log_error("OK ...", "")
					frappe.log_error("set_item_fields [warehouse]", wc_server.warehouses[0].warehouse)

			wc_server = frappe.get_cached_doc(
				"WooCommerce Server", self.woocommerce_product.woocommerce_server
			)
			if wc_server.item_field_map:
				woocommerce_product_dict = (
					self.woocommerce_product.deserialize_attributes_of_type_dict_or_list(
						self.woocommerce_product.to_dict()
					)
				)
    
				frappe.log_error("🔍 woocommerce_product_dict", woocommerce_product_dict)

    
    
				for map in wc_server.item_field_map:
					erpnext_item_field_name = map.erpnext_field_name.split(" | ")

					# We expect woocommerce_field_name to be valid JSONPath

					jsonpath_expr = parse(map.woocommerce_field_name)
					frappe.log_error("jsonpath_expr", jsonpath_expr)

					woocommerce_product_field_matches = jsonpath_expr.find(woocommerce_product_dict)

					# frappe.log_error("jsonpath_expr", type(str(jsonpath_expr)))

					if (str(jsonpath_expr) == "$.stock_quantity"): 
						frappe.log_error("🔹 Stock QTY", woocommerce_product_field_matches[0].value)
						setattr(item, erpnext_item_field_name[0], woocommerce_product_field_matches[0].value)
					elif (str(jsonpath_expr) == "$.categories.[*]"): 
						frappe.log_error("🔹 erpnext_item_field_name[categories]", erpnext_item_field_name[0])
						frappe.log_error("🔹 woocommerce_product_dict[categories]", woocommerce_product_dict['categories'])
						frappe.log_error("🔹 type - woocommerce_product_dict[categories]", type(json.dumps(woocommerce_product_dict['categories'])))

						setattr(item, erpnext_item_field_name[0], json.dumps(woocommerce_product_dict['categories']))
					else: 
						setattr(item, erpnext_item_field_name[0], woocommerce_product_field_matches[0].value)
	


					item_dirty = True
     
		return item_dirty, item

	def set_product_fields(
		self, woocommerce_product: WooCommerceProduct, item: ERPNextItemToSync
	) -> Tuple[bool, WooCommerceProduct]:
		"""
		If there exist any Field Mappings on `WooCommerce Server`, attempt to synchronise their values from
		ERPNext to WooCommerce

		Returns true if woocommerce_product was changed
		"""
		wc_product_dirty = False
		if item and woocommerce_product:
			wc_server = frappe.get_cached_doc("WooCommerce Server", woocommerce_product.woocommerce_server)
			if wc_server.item_field_map:

				# Deserialize the WooCommerce Product's list and dict fields because we want to potentially perform
				# in-place updates on the whole dict using jsonpath-ng. Use the existing class method for this.
				wc_product_with_deserialised_fields = (
					woocommerce_product.deserialize_attributes_of_type_dict_or_list(woocommerce_product)
				)

				for map in wc_server.item_field_map:
					erpnext_item_field_name = map.erpnext_field_name.split(" | ")
					erpnext_item_field_value = getattr(item.item, erpnext_item_field_name[0])

					# We expect woocommerce_field_name to be valid JSONPath
					jsonpath_expr = parse(map.woocommerce_field_name)
					woocommerce_product_field_matches = jsonpath_expr.find(wc_product_with_deserialised_fields)

					if len(woocommerce_product_field_matches) == 0:
						if woocommerce_product.name:
							# We're strict about existing WooCommerce Products, the field should exist
							raise ValueError(
								_("Field <code>{0}</code> not found in WooCommerce Product {1}").format(
									map.woocommerce_field_name, woocommerce_product.name
								)
							)
						else:
							# For new WooCommerce Products, the nested field may not exist yet, so don't stop the sync
							continue

					# JSONPath parsing typically returns a list, we'll only take the first value
					woocommerce_product_field_value = woocommerce_product_field_matches[0].value

					if erpnext_item_field_value != woocommerce_product_field_value:
						jsonpath_expr.update(wc_product_with_deserialised_fields, erpnext_item_field_value)
						wc_product_dirty = True

				if wc_product_dirty:
					# Re-serialize the WooCommerce Product's list and dict fields, because we deserialized earlier
					woocommerce_product = woocommerce_product.serialize_attributes_of_type_dict_or_list(
						wc_product_with_deserialised_fields
					)

		return wc_product_dirty, woocommerce_product

	def set_sync_hash(self):
		"""
		Set the last sync hash value using db.set_value, as it does not call the ORM triggers
		and it does not update the modified timestamp (by using the update_modified parameter)
		"""
		frappe.db.set_value(
			"Item WooCommerce Server",
			self.item.item_woocommerce_server.name,
			"woocommerce_last_sync_hash",
			self.woocommerce_product.woocommerce_date_modified,
			update_modified=False,
		)

		# If item was synchronised but the item is set not to sync, turn on the enabled flag
		# Items that are disabled for sync will still be synced if it is ordered on WooCommerce
		frappe.db.set_value(
			"Item WooCommerce Server",
			self.item.item_woocommerce_server.name,
			"enabled",
			1,
			update_modified=False,
		)


def get_list_of_wc_products(
	item: Optional[ERPNextItemToSync] = None, date_time_from: Optional[datetime] = None
) -> List[WooCommerceProduct]:
	"""
	Fetches a list of WooCommerce Products within a specified date range or linked with an Item, using pagination.

	At least one of date_time_from, item parameters are required
	"""
	if not any([date_time_from, item]):
		raise ValueError("At least one of date_time_from or item parameters are required")

	wc_records_per_page_limit = 100
	page_length = wc_records_per_page_limit
	new_results = True
	start = 0
	filters = []
	wc_products = []
	servers = None

	# Build filters
	if date_time_from:
		filters.append(["WooCommerce Product", "date_modified", ">", date_time_from])
	if item:
		filters.append(["WooCommerce Product", "id", "=", item.item_woocommerce_server.woocommerce_id])
		servers = [item.item_woocommerce_server.woocommerce_server]

	while new_results:
		woocommerce_product = frappe.get_doc({"doctype": "WooCommerce Product"})
		new_results = woocommerce_product.get_list(
			args={
				"filters": filters,
				"page_lenth": page_length,
				"start": start,
				"servers": servers,
				"as_doc": True,
			}
		)
		for wc_product in new_results:
			wc_products.append(wc_product)
		start += page_length
		if len(new_results) < page_length:
			new_results = []

	return wc_products


def get_item_price_rate(item: ERPNextItemToSync):
	"""
	Get the Item Price if Item Price sync is enabled
	"""
	# Check if the Item Price sync is enabled
	wc_server = frappe.get_cached_doc(
		"WooCommerce Server", item.item_woocommerce_server.woocommerce_server
	)
	if wc_server.enable_price_list_sync:
		item_prices = frappe.get_all(
			"Item Price",
			filters={"item_code": item.item.item_name, "price_list": wc_server.price_list},
			fields=["price_list_rate", "valid_upto"],
		)
		return next(
			(
				price.price_list_rate
				for price in item_prices
				if not price.valid_upto or price.valid_upto > now()
			),
			None,
		)


def clear_sync_hash_and_run_item_sync(item_code: str):
	"""
	Clear the last sync hash value using db.set_value, as it does not call the ORM triggers
	and it does not update the modified timestamp (by using the update_modified parameter)
	"""

	iws = frappe.qb.DocType("Item WooCommerce Server")

	iwss = (
		frappe.qb.from_(iws).where(iws.enabled == 1).where(iws.parent == item_code).select(iws.name)
	).run(as_dict=True)

	for iws in iwss:
		frappe.db.set_value(
			"Item WooCommerce Server",
			iws.name,
			"woocommerce_last_sync_hash",
			None,
			update_modified=False,
		)

	if len(iwss) > 0:
		run_item_sync(item_code=item_code, enqueue=True)
