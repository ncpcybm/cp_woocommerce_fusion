function is_wc_enabled_wrapper(fnCallback) {

	frappe.db.get_single_value("CP Settings", "woocommerce_sync").then((value) => {
		if (value) {
			fnCallback();
		}
	});
}

frappe.ui.form.on('Item', {
	onload_post_render: function (frm) {

	},
	refresh: function (frm) {

		is_wc_enabled_wrapper(() => {
			// Add a custom button to sync Item Stock with WooCommerce
			frm.add_custom_button(__("🔄 Sync this Item's Stock Levels <b style='color:red;text-decoration: underline;text-decoration-thickness: 2px;'>to</b> WooCommerce"), function () {
				frm.trigger("sync_item_stock");
			}, __('Actions'));

			// frm.add_custom_button(__("🔄 Sync this Item's Stock <b style='color:red;text-decoration: underline;text-decoration-thickness: 2px;'>from</b> WooCommerce"), function () {
			// 	frm.trigger("sync_item_stock_from_woocommerce");
			// }, __('Actions'));

			// // Add a custom button to sync Item Price with WooCommerce
			// frm.add_custom_button(__("Sync this Item's Price to WooCommerce"), function () {
			// 	frm.trigger("sync_item_price");
			// }, __('Actions'));

			// Add a custom button to sync Item with WooCommerce
			// frm.add_custom_button(__("Sync this Item with WooCommerce"), function () {
			// 	frm.trigger("sync_item");
			// }, __('Actions'));
		})
	},

	sync_item_stock_from_woocommerce: function (frm) {

		is_wc_enabled_wrapper(() => {
			console.log(frm.doc)


			if (frm.doc.custom_woocomm_synced) {

				frappe.msgprint({
					title: __('Notification'),
					indicator: 'red',
					message: __('Item is already Synced with WooCommerce. System can not proceed with stock synchronization.')
				});
				return;
			}


						frappe.dom.freeze(__("Sync Item Stock to WooCommerce ..."));


			const myHeaders = new Headers();
			myHeaders.append("Authorization", "token b7fb543d744f4f0:1323aa2545e8096");
			myHeaders.append("Content-Type", "application/json");
			myHeaders.append("X-Frappe-CSRF-Token", frappe.csrf_token);

			const raw = JSON.stringify({
				"id": frm.woocomm_product_id
			});

			const requestOptions = {
				method: "POST",
				headers: myHeaders,
				body: raw,
				redirect: "follow"
			};

			fetch("https://staging.cpherbalist.com/api/method/woocommerce_fusion.tasks.stock_update.sync_stock_from_woocommerce", requestOptions)
				.then((response) => response.text())
				.then((result) => {
					frappe.dom.unfreeze();
					frappe.show_alert({
						message: __('Synchronized stock level from WooCommerce'),
						indicator: 'green'
					}, 8);
					frm.reload_doc();
				})
				.catch((error) => {
					frappe.dom.unfreeze();
					frappe.show_alert({
						message: __('There was an error processing the request. See Error Log.'),
						indicator: 'red'
					}, 8);
				});



		});
	},
	sync_item_stock: function (frm) {
		// Sync this Item
		is_wc_enabled_wrapper(() => {

			frappe.dom.freeze(__("Sync Item Stock with WooCommerce..."));
			frappe.call({
				method: "woocommerce_fusion.tasks.stock_update.update_stock_levels_on_woocommerce",
				args: {
					doc: JSON.stringify(frm.doc)
				},
				callback: function (r) {
					frappe.dom.unfreeze();
					frappe.show_alert({
						message: __('Synchronized stock level to WooCommerce'),
						indicator: 'green'
					}, 8);
					frm.reload_doc();
				},
				error: (r) => {
					frappe.dom.unfreeze();
					frappe.show_alert({
						message: __('There was an error processing the request. See Error Log.'),
						indicator: 'red'
					}, 8);
				}
			});

			return;

			frappe.dom.freeze(__("Sync Item Stock with WooCommerce..."));
			frappe.call({
				method: "woocommerce_fusion.tasks.stock_update.update_stock_levels_on_woocommerce_site",
				args: {
					item_code: frm.doc.name
				},
				callback: function (r) {
					frappe.dom.unfreeze();
					frappe.show_alert({
						message: __('Synchronised stock level to WooCommerce for enabled servers'),
						indicator: 'green'
					}, 5);
					frm.reload_doc();
				},
				error: (r) => {
					frappe.dom.unfreeze();
					frappe.show_alert({
						message: __('There was an error processing the request. See Error Log.'),
						indicator: 'red'
					}, 5);
				}
			});
		})
	},

	sync_item_price: function (frm) {
		// Sync this Item's Price
		frappe.dom.freeze(__("Sync Item Price with WooCommerce..."));
		frappe.call({
			method: "woocommerce_fusion.tasks.sync_item_prices.run_item_price_sync",
			args: {
				item_code: frm.doc.name
			},
			callback: function (r) {
				frappe.dom.unfreeze();
				frappe.show_alert({
					message: __('Synchronised item price to WooCommerce'),
					indicator: 'green'
				}, 5);
				frm.reload_doc();
			},
			error: (r) => {
				frappe.dom.unfreeze();
				frappe.show_alert({
					message: __('There was an error processing the request. See Error Log.'),
					indicator: 'red'
				}, 5);
			}
		});
	},

	sync_item: function (frm) {
		// Sync this Item
		frappe.dom.freeze(__("Sync Item with WooCommerce..."));
		frappe.call({
			method: "woocommerce_fusion.tasks.sync_items.run_item_sync",
			args: {
				item_code: frm.doc.name
			},
			callback: function (r) {
				frappe.dom.unfreeze();
				frappe.show_alert({
					message: __('Sync completed successfully'),
					indicator: 'green'
				}, 5);
				frm.reload_doc();
			},
			error: (r) => {
				frappe.dom.unfreeze();
				frappe.show_alert({
					message: __('There was an error processing the request. See Error Log.'),
					indicator: 'red'
				}, 8);
			}
		});
	},
})

frappe.ui.form.on('Item WooCommerce Server', {
	view_product: function (frm, cdt, cdn) {
		let current_row_doc = locals[cdt][cdn];
		console.log(current_row_doc);
		frappe.set_route("Form", "WooCommerce Product", `${current_row_doc.woocommerce_server}~${current_row_doc.woocommerce_id}`);
	}
})