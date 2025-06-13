frappe.call({
    method: "frappe.client.get_list",
    args: {
        doctype: "WooCommerce Server",
        filters: {
            is_primary: 1
        },
        fields: ["*"]
    },
    callback: function(response) {
        const wc_servers = response.message;
        if (wc_servers && wc_servers.length > 0) {
            wc_servers.forEach(function(server) {
                console.log(server);
                console.log("API Consumer Key:", server.api_consumer_key);
                console.log("API Consumer Secret:", server.api_consumer_secret);
            });
        } else {
            console.log("No primary WooCommerce servers found.");
        }
    }
});
