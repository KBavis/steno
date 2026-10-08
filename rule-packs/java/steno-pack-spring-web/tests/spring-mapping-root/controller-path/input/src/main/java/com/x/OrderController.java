package com.x;

import org.springframework.web.bind.annotation.*;

@RestController
@RequestMapping("/orders")
public class OrderController {
    @GetMapping("/{id}")
    public String get(@PathVariable String id) { return id; }

    @PostMapping(value = Paths.BULK, consumes = "application/json")
    public void bulk(@RequestBody String body) {}

    @PostMapping
    public void create(@RequestBody String body) {}

    @GetMapping(produces = "application/json")
    public String list() { return ""; }

    @RequestMapping(value = "/{id}/status", method = RequestMethod.PUT)
    public void status(@PathVariable String id) {}
}
