"""Imported only through injected entry points in tests: simulates a plugin that crashes on
import. Never registered in any installed package."""

raise ImportError("simulated: plugin dependency missing")
