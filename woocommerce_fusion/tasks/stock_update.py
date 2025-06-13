import json
import math

import frappe

import requests
from woocommerce_fusion.tasks.utils import APIWithRequestLogging

from frappe.utils import nowdate, nowtime

# ===============================================================================================================


@frappe.whitelist(allow_guest=False)
def sync_stock_from_woocommerce():
	try:
		if frappe.session.user == "Guest":
			frappe.throw(frappe.PermissionError)
		
		request_obj = frappe.form_dict
		woo_product_id = request_obj.get("id")


		# Map Woo Product id to erp product 
		erp_product_name = frappe.db.get_value("Item", {"woocomm_product_id": woo_product_id}, ["name"])
		is_stock_item = frappe.db.get_value("Item", {"woocomm_product_id": woo_product_id}, ["is_stock_item"])


		frappe.log_error("sync_stock_from_woocommerce",woo_product_id)

		url = f"https://staging1.cpherbalist.com/wp-json/wc/v3/stock/{woo_product_id}?consumer_key=ck_59bc701926376bf624fe8d343816f69b0286d198&consumer_secret=cs_bf66f522851721f946ea6cfc3b7ce3615d6506df"

		payload = {}
		headers = {}

		response = requests.request("GET", url, headers=headers, data=payload)

		data = response.json()
		stock_quantity = data.get("stock_quantity")
		stock_status = data.get("stock_status")


		frappe.log_error("erp_product_name ", erp_product_name)
		frappe.log_error("is_stock_item ", is_stock_item)


		frappe.log_error("url ", url)
		frappe.log_error("stock_quantity ", stock_status)
		frappe.log_error("Stock Quantity:", stock_quantity)

		s_warehouse = None
		t_warehouse = None
		company = None

		if response.text: 


			if not is_stock_item:
				frappe.log_error("THIS IS NOT A STOCK ITEM ", 'This is not a stock item thus we have to update woocommerce_qty')
				item = frappe.get_doc("Item", erp_product_name)
				item.woocommerce_qty = stock_quantity
				item.save()
				frappe.log_error(f"WooCommerce quantity for {erp_product_name} updated to {stock_quantity}")
				return {
					"success" : True,
					"item_type": 'bundle',
					"item_name": erp_product_name,
					"message": "Item is bundle and QTY fetch as virtual stock.",
				}
			else:
				# Get WooCommerce Sever
				wc_server = frappe.get_all("WooCommerce Server",filters={"is_primary": 1}, fields = ["*"])

				for server in wc_server:
					frappe.log_error("woocommerce_server_url ", server.woocommerce_server_url)
					frappe.log_error("api_consumer_key ", server.api_consumer_key)
					frappe.log_error("api_consumer_secret ", server.api_consumer_secret)
					frappe.log_error("warehouse ", server.warehouse)
					s_warehouse = server.warehouse
					t_warehouse = server.warehouse
					company = server.company

				stock_entry = frappe.new_doc("Stock Entry")
				item = frappe.get_doc("Item", erp_product_name)


				if item.custom_woocomm_synced: 
					return {
						"success" : True,
						"item_type": 'bundle',
						"item_name": erp_product_name,
						"message": "Item is already synced with WooCommerce.",
					}

				item.woocommerce_qty = stock_quantity
				item.custom_woocomm_synced = 1
				item.save()

				stock_entry.purpose = "Material Receipt"  # or "Material Receipt", "Material Issue", etc.
				stock_entry.stock_entry_type = "Material Receipt"
				
				stock_entry.company = company

				# Add an item
				stock_entry.append("items", {
					"item_code": erp_product_name,
					"qty": stock_quantity,
					"s_warehouse": s_warehouse,
					"t_warehouse": t_warehouse
				})

				stock_entry.add_comment("Comment", "Stock from WooCommerce (API)")
				
				# Save and submit
				stock_entry.save()
				stock_entry.submit()

				frappe.log_error("✅ stock_entry " + stock_entry.name + "...", stock_entry)
				return {
					"success" : True,
					"item_type": 'bundle',
					"item_name": erp_product_name,
					"message": "Item is already synced with WooCommerce.",
					"stock_entry" : stock_entry.name
				}
		else: 
			return "Fail to create stock entry"
	except frappe.ValidationError as ve:
		frappe.log_error(f"⚠️ (468) Validation Error", ve)
	except frappe.PermissionError as pe:
		frappe.log_error(f"🚫 (470) Permission Denied",pe)
	except Exception as e:
		frappe.log_error("Stock update exception", str(frappe.get_traceback()))
		frappe.throw("An error occurred while updating stock.")


@frappe.whitelist(allow_guest=False)
def update_woocommerce_stock():
    try:
        if frappe.session.user == "Guest":
            frappe.throw(frappe.PermissionError)

        request_obj = frappe.form_dict

        woo_product_id = request_obj.get("id")
        stock_quantity = float(request_obj.get("stock_quantity"))

        frappe.log_error('get_header', str(frappe.request.headers))
        frappe.log_error('woo_product_id', str(woo_product_id))


        # Get the item with the matching WooCommerce product ID
        result = frappe.get_all(
            'Item',
            filters={'woocomm_product_id': woo_product_id},
            fields=['name']
        )

        if not result:
            frappe.throw(woo_product_id)

        item_code = result[0]['name']
        frappe.log_error("[ITEM]", item_code)

        # Get the current quantity in the specified warehouse
        qty_data = frappe.db.sql("""
            SELECT warehouse, actual_qty FROM `tabBin`
            WHERE item_code = %s AND warehouse = 'WooCommerce - CP'
        """, (item_code,), as_dict=1)

        curr_system_stock = qty_data[0]["actual_qty"] if qty_data else 0
        frappe.log_error("curr_system_stock", str(curr_system_stock))
        frappe.log_error("stock_quantity", str(stock_quantity))

        # Create Stock Reconciliation
        sr = frappe.new_doc("Stock Reconciliation")
        sr.purpose = "Stock Reconciliation"
        sr.posting_date = nowdate()
        sr.posting_time = nowtime()
        sr.set_posting_time = 1
        sr.company = 'CHRYSTALLENA POULLI HERBAL SKIN CARE PRODUCTS LTD'
        sr.expense_account = 'Stock Adjustment - CP'

        sr.append("items", {
            "item_code": item_code,
            "warehouse": 'WooCommerce - CP',
            "qty": stock_quantity
        })

        sr.insert()
        sr.submit()

        frappe.log_error("[product.update.stock]", str(request_obj))
        return {"message": "ok"}

    except Exception as e:
        frappe.log_error("Stock update exception", str(frappe.get_traceback()))
        frappe.throw("An error occurred while updating stock.")


def update_stock_levels_for_woocommerce_item(doc, method):
	if not frappe.flags.in_test:
		if doc.doctype in ("Stock Entry", "Stock Reconciliation", "Sales Invoice", "Delivery Note"):
			# Check if there are any enabled WooCommerce Servers with stock sync enabled
			if (
				len(
					frappe.get_list(
						"WooCommerce Server", filters={"enable_sync": 1, "enable_stock_level_synchronisation": 1}
					)
				)
				> 0
			):
				if doc.doctype == "Sales Invoice":
					if doc.update_stock == 0:
						return
				item_codes = [row.item_code for row in doc.items]
				for item_code in item_codes:
					frappe.enqueue(
						"woocommerce_fusion.tasks.stock_update.update_stock_levels_on_woocommerce_site",
						enqueue_after_commit=True,
						item_code=item_code,
					)

def get_value(data, key_path):
    """
    Retrieve a value from nested dicts/lists using a key path.
    
    key_path: list of keys and/or indices.
    Example: ["item_defaults", 0, "default_warehouse"]
    """
    try:
        for key in key_path:
            data = data[key]
        return data
    except (KeyError, IndexError, TypeError):
        return None  # or raise an error/log
	

@frappe.whitelist()
def update_stock_levels_on_woocommerce(doc, method = None):
	


	try:
		python_obj = json.loads(doc)
		frappe.log_error(f"Document ", python_obj)

		WAREHOUSE = "WooCommerce - CP"  
		to_warehouse = WAREHOUSE
		woocomm_product_id = get_value(python_obj, ["woocomm_product_id"])
		item_code = get_value(python_obj, ["item_code"])


		# if doc.doctype == 'Stock Ledger Entry': 
		# 	to_warehouse = doc.warehouse
		# elif doc.doctype == 'Stock Entry': 
		# 	to_warehouse = doc.to_warehouse


		# frappe.log_error(f"doc.item_code : {doc.item_code}")
		# frappe.log_error(f"doc.woocomm_product_id : {doc.woocomm_product_id}")


		if to_warehouse == None:
			frappe.log_error("Stock update exception")
			frappe.throw("An error occurred while updating stock.")

		if to_warehouse == WAREHOUSE:

			erp_product_name = woocomm_product_id 

			bin_qty = frappe.db.get_value("Bin", {"item_code": item_code, "warehouse": to_warehouse}, "actual_qty")
			actual_qty = frappe.db.get_value("Bin", {"item_code": item_code, "warehouse": to_warehouse}, "actual_qty")
			

			frappe.log_error(f"actual_qty", actual_qty)

			# frappe.log_error(f"erp_product_name", erp_product_name)
			# frappe.log_error(f"Stock changed in {WAREHOUSE}: Item {doc.item_code}, Qty: {doc.actual_qty}", "Stock Change Alert")
			# frappe.log_error(f"Updated stock for {doc.item_code} in {doc.warehouse}: {bin_qty} with {actual_qty + doc.actual_qty} ({doc.actual_qty})", "Stock Info")

			url = f"https://staging1.cpherbalist.com/wp-json/wc/v3/stock/{erp_product_name}/update?consumer_key=ck_59bc701926376bf624fe8d343816f69b0286d198&consumer_secret=cs_bf66f522851721f946ea6cfc3b7ce3615d6506df"
			frappe.log_error("url", url)

			payload = json.dumps({
				"stock": actual_qty
			})
			headers = {
			'Content-Type': 'application/json'
			}

			response = requests.request("PUT", url, headers=headers, data=payload)

			frappe.log_error(response.text)

	except Exception as e:
			frappe.log_error("Stock update exception", str(frappe.get_traceback()))
			frappe.throw("An error occurred while updating stock.")

# ===============================================================================================================


def update_stock_levels_for_all_enabled_items_in_background():
	"""
	Get all enabled ERPNext Items and post stock updates to WooCommerce
	"""
	erpnext_items = []
	current_page_length = 500
	start = 0

	# Get all items, 500 records at a time
	while current_page_length == 500:
		items = frappe.db.get_all(
			doctype="Item",
			filters={"disabled": 0},
			fields=["name"],
			start=start,
			page_length=500,
		)
		erpnext_items.extend(items)
		current_page_length = len(items)
		start += current_page_length

	for item in erpnext_items:
		frappe.enqueue(
			"woocommerce_fusion.tasks.stock_update.update_stock_levels_on_woocommerce_site",
			item_code=item.name,
		)


@frappe.whitelist()
def update_stock_levels_on_woocommerce_site(item_code):
	"""
	Updates stock levels of an item on all its associated WooCommerce sites.

	This function fetches the item from the database, then for each associated
	WooCommerce site, it retrieves the current inventory, calculates the new stock quantity,
	and posts the updated stock levels back to the WooCommerce site.
	"""
	item = frappe.get_doc("Item", item_code)

	if len(item.woocommerce_servers) == 0 or not item.is_stock_item or item.disabled:
		return False
	else:
		bins = frappe.get_list(
			"Bin", {"item_code": item_code}, ["name", "warehouse", "reserved_qty", "actual_qty"]
		)

		for wc_site in item.woocommerce_servers:
			if wc_site.woocommerce_id:
				woocommerce_id = wc_site.woocommerce_id
				woocommerce_server = wc_site.woocommerce_server
				wc_server = frappe.get_cached_doc("WooCommerce Server", woocommerce_server)

				if (
					not wc_server
					or not wc_server.enable_sync
					or not wc_site.enabled
					or not wc_server.enable_stock_level_synchronisation
				):
					continue

				wc_api = APIWithRequestLogging(
					url=wc_server.woocommerce_server_url,
					consumer_key=wc_server.api_consumer_key,
					consumer_secret=wc_server.api_consumer_secret,
					version="wc/v3",
					timeout=40,
				)

				# Sum all quantities from select warehouses and round the total down (WooCommerce API doesn't accept float values)
				data_to_post = {
					"stock_quantity": math.floor(
						sum(
							bin.actual_qty
							if not wc_server.subtract_reserved_stock
							else bin.actual_qty - bin.reserved_qty
							for bin in bins
							if bin.warehouse in [row.warehouse for row in wc_server.warehouses]
						)
					)
				}

				try:
					parent_item_id = item.variant_of
					if parent_item_id:
						parent_item = frappe.get_doc("Item", parent_item_id)
						# Get the parent item's woocommerce_id
						for parent_wc_site in parent_item.woocommerce_servers:
							if parent_wc_site.woocommerce_server == woocommerce_server:
								parent_woocommerce_id = parent_wc_site.woocommerce_id
								break
						if not parent_woocommerce_id:
							continue
						endpoint = f"products/{parent_woocommerce_id}/variations/{woocommerce_id}"
					else:
						endpoint = f"products/{woocommerce_id}"
					response = wc_api.put(endpoint=endpoint, data=data_to_post)
				except Exception as err:
					error_message = f"{frappe.get_traceback()}\n\nData in PUT request: \n{str(data_to_post)}"
					frappe.log_error("WooCommerce Error", error_message)
					raise err
				if response.status_code != 200:
					error_message = f"Status Code not 200\n\nData in PUT request: \n{str(data_to_post)}"
					error_message += (
						f"\n\nResponse: \n{response.status_code}\nResponse Text: {response.text}\nRequest URL: {response.request.url}\nRequest Body: {response.request.body}"
						if response is not None
						else ""
					)
					frappe.log_error("WooCommerce Error", error_message)
					raise ValueError(error_message)

		return True
