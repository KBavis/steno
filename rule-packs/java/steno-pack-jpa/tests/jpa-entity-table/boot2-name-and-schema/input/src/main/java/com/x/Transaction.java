package com.x;

import javax.persistence.*;

@Entity
@Table(name = "transaction", schema = "budget")
public class Transaction { @Id private String id; }
