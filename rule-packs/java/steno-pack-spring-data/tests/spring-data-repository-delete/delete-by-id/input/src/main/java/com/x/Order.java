package com.x;

import jakarta.persistence.*;

@Entity
@Table(name = "orders")
public class Order { @Id private Long id; }
