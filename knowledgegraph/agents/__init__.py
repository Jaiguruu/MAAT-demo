"""LangGraph multi-agent question-answering on top of the knowledge graph.

The supervisor routes a user question to specialized workers - each backed by
one or more graph tools - and a synthesizer turns the collected evidence into
the final answer. Model calls go through the OpenAI SDK.
"""
