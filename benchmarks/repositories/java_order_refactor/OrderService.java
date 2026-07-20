public final class OrderService {
    private final NotificationPort notificationPort;

    public OrderService(NotificationPort notificationPort) {
        this.notificationPort = notificationPort;
    }

    public OrderResult place(Order order) {
        // Deliberate design defect: validation, pricing, and notification are
        // mixed in this method even though the observable behavior is correct.
        if (order == null) {
            throw new IllegalArgumentException("order is required");
        }
        if (order.subtotalCents() < 0) {
            throw new IllegalArgumentException("subtotal must not be negative");
        }
        if (order.email() == null || order.email().isBlank()) {
            throw new IllegalArgumentException("email is required");
        }

        int finalTotal = order.subtotalCents();
        if (order.student() && order.subtotalCents() >= 10_000) {
            finalTotal = order.subtotalCents() * 90 / 100;
        }

        OrderResult result = new OrderResult(order.id(), finalTotal);
        notificationPort.sendOrderPlaced(
            order.email(),
            result.orderId(),
            result.finalTotalCents()
        );
        return result;
    }
}

