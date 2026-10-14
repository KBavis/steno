package com.x;

import java.util.Map;

public class OrderService {
    private final OrderRepository orders;
    private final Map<Long, Order> cache;

    public OrderService(OrderRepository orders, Map<Long, Order> cache) {
        this.orders = orders;
        this.cache = cache;
    }

    public Order place(Order order) {
        cache.get(1L);
        return orders.save(order);
    }

    public void cancel(Long id) {
        orders.findByCustomer("x");
        orders.updateStatus(id, "CANCELLED");
        orders.deleteById(id);
    }
}
