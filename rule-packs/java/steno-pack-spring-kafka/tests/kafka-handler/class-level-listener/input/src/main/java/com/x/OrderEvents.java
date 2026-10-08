package com.x;

import org.apache.kafka.clients.consumer.KafkaConsumer;
import org.apache.kafka.clients.producer.KafkaProducer;
import org.apache.kafka.clients.producer.ProducerRecord;
import org.springframework.kafka.annotation.KafkaListener;
import org.springframework.kafka.core.KafkaTemplate;
import org.springframework.messaging.handler.annotation.SendTo;
import java.util.List;

public class OrderEvents {
    private final KafkaTemplate<String, String> kafka;
    private final KafkaProducer<String, String> producer;
    private final KafkaConsumer<String, String> consumer;

    public OrderEvents(KafkaTemplate<String, String> kafka, KafkaProducer<String, String> producer, KafkaConsumer<String, String> consumer) {
        this.kafka = kafka; this.producer = producer; this.consumer = consumer;
    }

    @KafkaListener(topics = "${app.topics.orders}", groupId = "billing")
    @SendTo("orders.validated")
    public String onOrder(String order) {
        kafka.send("audit", order, order);
        producer.send(new ProducerRecord<>("raw-orders", order));
        return order;
    }

    @KafkaListener(topics = {"refunds", "returns"})
    public void onRefund(String refund) {}

    public void poll() {
        consumer.subscribe(List.of("backfill"));
    }
}
