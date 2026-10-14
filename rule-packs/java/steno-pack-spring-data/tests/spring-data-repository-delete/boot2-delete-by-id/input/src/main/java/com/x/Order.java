package com.x;

import javax.persistence.*;

@Entity
@Table(name = "orders")
public class Order { @Id private Long id; }
