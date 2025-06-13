frappe.ui.form.on("WooCommerce Product", {
	refresh(frm) {


		frappe.call({
                method: "frappe.client.get_list",
                args: {
                    doctype: "Item",
                    fields: ["name", "item_name"],
                    filters: {
                        woocomm_product_id: cur_frm.doc.woocommerce_id
                    },
                    limit_page_length: 1
                },
                callback: function(response) {
                    if (response.message && response.message.length > 0) {
                        const item = response.message[0];

                        // Add a custom button
                        cur_frm.add_custom_button(__('📦 View Product: ' + item.item_name), function() {
                            frappe.set_route("Form", "Item", item.name);
                        }); // Optional group
                    }
                }
            });


		// Add a custom button to sync this WooCommerce order to a Sales Order
		// frm.add_custom_button(__("🔄 Sync this Item to ERP"), function () {
		// 	frm.trigger("sync_product");
		// }, __('Actions'));

		// Set intro text
		const intro_txt = __(
			"🚨 Note: Saving changes on this document will update this resource on WooCommerce."
		);
		frm.set_intro(intro_txt, "red");
	},

	sync_product: function(frm) {
		// Sync this WooCommerce Product
		frappe.dom.freeze(__("🔄 Sync Product with ERP ..."));
		frappe.call({
			method: "woocommerce_fusion.tasks.sync_items.run_item_sync",
			args: {
				woocommerce_product_name: frm.doc.name
			},
			callback: function(r) {
				console.log(r);
				frappe.dom.unfreeze();
				frappe.show_alert({
					message:__('Sync completed successfully'),
					indicator:'green'
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
});

