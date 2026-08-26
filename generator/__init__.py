"""HFB Proactive AI Chatbot — synthetic test-dataset generator.

See ../POA/16_Test_Dataset_Strategy.md for the design this implements.

Layers (each consumes the previous):
    seed -> WorldBuilder -> CustomerFactory -> SessionSimulator
         -> ScenarioComposer (golden, Tier A) / VolumeSampler (Tier B)

Everything is derived from ONE seeded reference world so events, LLM claims and
the booking-API mock stay internally consistent.
"""

__all__ = ["__version__"]
__version__ = "0.1.0"
