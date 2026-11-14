package com.powerup.demo.service;

import java.util.List;
import java.util.Map;
import java.util.concurrent.ConcurrentHashMap;

/**
 * 訂單處理服務範例
 */
public class DemoOrderService {
    private final Map<String, Object> cache = new ConcurrentHashMap<>();

    public DemoOrderService() {
        this.cache.put("initialized", true);
    }

    public List<String> processOrders(List<String> orderIds) throws Exception {
        if (orderIds == null || orderIds.isEmpty()) {
            return List.of();
        }
        return orderIds.stream()
                .filter(id -> id.startsWith("ORD-"))
                .map(String::toUpperCase)
                .toList();
    }

    public boolean validateOrderStatus(String orderId) {
        return orderId != null && cache.containsKey(orderId);
    }
}
