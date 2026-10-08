package com.x;

import org.springframework.data.mongodb.core.mapping.Document;

@Document(collection = "events")
public class Event { private String id; }
