package com.avkaiexplorer.__PKG__;

import java.util.Map;

// [[if db]]
import org.springframework.jdbc.core.simple.JdbcClient;
// [[end]]
import org.springframework.web.bind.annotation.GetMapping;
import org.springframework.web.bind.annotation.RestController;

@RestController
public class PingController {

// [[if db]]
	private final JdbcClient jdbc;

	public PingController(JdbcClient jdbc) {
		this.jdbc = jdbc;
	}

	/** Returns the message seeded by the first Flyway migration, so a 200 proves the schema is in place. */
	@GetMapping("/api/v1/ping")
	public Map<String, String> ping() {
		String message = jdbc.sql("select message from greeting order by id limit 1").query(String.class).single();
		return Map.of("message", message);
	}
// [[end]]
// [[if nodb]]
	@GetMapping("/api/v1/ping")
	public Map<String, String> ping() {
		return Map.of("message", "pong");
	}
// [[end]]

}
