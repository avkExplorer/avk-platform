# The AVK Keycloak image: the pinned upstream image + `kc.sh build` already run (so it can start with a read-only root
# filesystem and `start --optimized`) + the AVK theme. Built on this PC by `avk auth build`; nothing is pulled at run time.
# Base: Keycloak 26.8.2. Update the digest here, rebuild, then run the auth tests (RUNBOOK: "upgrade Keycloak").
FROM quay.io/keycloak/keycloak@sha256:7e66a96e4739239070aeaf72e4c5decf0f7f682cfc725b9f7ea2b336eaba38b3

# Build-time options: they are baked in, so the same values are not needed again at start.
ENV KC_DB=postgres \
    KC_HEALTH_ENABLED=true \
    KC_METRICS_ENABLED=true

COPY themes/ /opt/keycloak/themes/
RUN /opt/keycloak/bin/kc.sh build

ENTRYPOINT ["/opt/keycloak/bin/kc.sh"]
