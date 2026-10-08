package com.x;

import org.springframework.amqp.rabbit.annotation.RabbitListener;
import org.springframework.amqp.rabbit.core.RabbitTemplate;
import org.springframework.jms.annotation.JmsListener;
import org.springframework.jms.core.JmsTemplate;

public class Queues {
    private final JmsTemplate jms;
    private final RabbitTemplate rabbit;

    public Queues(JmsTemplate jms, RabbitTemplate rabbit) { this.jms = jms; this.rabbit = rabbit; }

    @JmsListener(destination = "invoices.in")
    public void onInvoice(String m) {
        jms.convertAndSend("invoices.out", m);
    }

    @RabbitListener(queues = "emails")
    public void onEmail(String m) {
        rabbit.convertAndSend("emails.sent", m);
        rabbit.convertAndSend("exchange", "routing.key", m);
    }
}
