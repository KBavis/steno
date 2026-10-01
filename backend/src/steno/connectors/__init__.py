"""Connectors: a source system plus a scope (D30). They fetch; they don't interpret.

Phase 1: Bitbucket (the target app) and GitHub (for public test repos). Each connector
clones repositories for ingestion and reads files at an ingested commit for the code
tools (no file contents are stored, D17).
"""
