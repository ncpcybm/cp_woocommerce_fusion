frappe.listview_settings['Item'] = {
    onload: function (listview) {
        listview.page.add_inner_button("🔄 Sync Stock <b style='color:red; text-decoration:underline'>from</b> WooCommerce (Selected)", () => {
            const selected = listview.get_checked_items();

            if (selected.length === 0) {
                frappe.msgprint('No items selected.');
                return;
            }

            selected.forEach(element => {
                console.log(element)


                const myHeaders = new Headers();
                myHeaders.append("Authorization", "token b7fb543d744f4f0:1323aa2545e8096");
                myHeaders.append("Content-Type", "application/json");
                myHeaders.append("X-Frappe-CSRF-Token", frappe.csrf_token);

                const raw = JSON.stringify({
                    "id": element.woocomm_product_id
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
                        _res = JSON.parse(result)
                        console.log(_res.message)

                        if (_res.message.success) {

                            if (_res.message.stock_entry) {
                                frappe.msgprint(`Item ${element.item_name} synced, Material Entry: <a href="app/stock-entry/${_res.message.stock_entry}" target="_blank">${_res.message.stock_entry}</a>`);
                            } else {
                                
                                frappe.msgprint(_res.message.message);

                            }

                        }


                    })
                    .catch((error) => console.error(error));


            });

            const names = selected.map(item => item.name).join(', ');
            //frappe.msgprint(`Selected Items: ${names}`);

        });
    }
};