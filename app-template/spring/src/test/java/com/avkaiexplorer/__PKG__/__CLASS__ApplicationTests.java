package com.avkaiexplorer.__PKG__;

import static io.restassured.RestAssured.given;
import static org.hamcrest.Matchers.equalTo;
import static org.hamcrest.Matchers.containsString;

import io.restassured.RestAssured;
import org.junit.jupiter.api.BeforeEach;
import org.junit.jupiter.api.Test;
import org.springframework.beans.factory.annotation.Value;
import org.springframework.boot.test.context.SpringBootTest;

/** API tests over real HTTP against the running application (RestAssured) with an in-memory database. */
@SpringBootTest(webEnvironment = SpringBootTest.WebEnvironment.RANDOM_PORT)
class __CLASS__ApplicationTests {

	@Value("${local.server.port}")
	int port;

	@BeforeEach
	void setUp() {
		RestAssured.port = port;
	}

	@Test
	void pingReturnsTheSeededMessage() {
		given().when().get("/api/v1/ping").then().statusCode(200).body("message", equalTo("pong"));
	}

	@Test
	void livenessIsUp() {
		given().when().get("/healthz").then().statusCode(200).body("status", equalTo("up"));
	}

	@Test
	void readinessChecksTheDatabase() {
		given().when().get("/readyz").then().statusCode(200).body("status", equalTo("ready"));
	}

	// [[if web]]
	@Test
	void rootPageIsServed() {
		given().when().get("/").then().statusCode(200).body(containsString("__APP__"));
	}
	// [[end]]

	// [[if auth]]
	@Test
	void unknownPathsAnswerWithoutLeakingDetails() {
		// Everything that is not public needs a login first, so an unknown path looks exactly like a protected one (401).
		given().when().get("/does-not-exist").then().statusCode(401).body(org.hamcrest.Matchers.not(containsString("Exception")));
	}
	// [[end]]

}
