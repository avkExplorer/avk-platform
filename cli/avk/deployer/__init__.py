"""The pull-based deployer: runs on the owner's PC, never receives a connection from outside.

A GitHub Deployment is only a trigger. A digest reaches production only after `trust.check` proves the build signature and
the owner's approval attestation, and only after it passes the pre- and post-switch smoke tests (`bluegreen`).
"""
