package com.x;

import jakarta.persistence.*;

@Entity
@Table(name = "transaction", schema = "budget")
public class Transaction { @Id private String id; }
