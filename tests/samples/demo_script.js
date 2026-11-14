/**
 * Demo Script
 */
class OrderController {
    constructor(apiEndpoint) {
        this.apiEndpoint = apiEndpoint;
        this.activeOrders = [];
    }

    async fetchOrders() {
        const res = await fetch(this.apiEndpoint);
        this.activeOrders = await res.json();
        return this.activeOrders;
    }
}

const renderCards = (orders) => {
    return orders.map(o => `<div>${o.id}</div>`);
};
