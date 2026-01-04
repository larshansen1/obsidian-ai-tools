---
title: "Obsidian AI Tools - YouTube Video Ingestion with LLM-Generated Notes"
tags:
  - obsidian
  - ai
  - llm
  - youtube
  - cli
  - knowledge-management
  - python
  - openrouter
  - mcp
  - transcript-processing
created: 2026-01-02T21:30:10.077404
author: Local File
type: source-note
source_type: web
source_url: file:///Users/larshansen/Documents/Dev/Coding/obsidian-ai-tools/README.md
model: anthropic/claude-sonnet-4
prompt_version: markdown_v1
---

# Obsidian AI Tools - YouTube Video Ingestion with LLM-Generated Notes

## Summary

A Python CLI tool that fetches YouTube video transcripts and uses OpenRouter (Claude 3.5 Sonnet) to generate structured Obsidian notes with comprehensive metadata tracking. Built as a Week 1 MVP using a pragmatic functional architecture with pure functions designed for easy Model Context Protocol integration.

## Key Claims

- Uses a 'pragmatic functional architecture' optimized for fast iteration and MCP-ready pure functions
- Current coverage threshold is set to 45% for MVP, with production target of 85%+
- Built following the Tracer Bullet Approach where simplicity and shipping quickly are prioritized over perfection
- Every note includes comprehensive provenance tracking: source URL, timestamp, LLM model used, prompt version, and source type

## Key Points

- CLI command 'kai ingest <url>' fetches YouTube transcripts and generates structured notes using OpenRouter API
- Generated notes follow consistent schema with frontmatter including type, source_type, source_url, created timestamp, model, and prompt_version
- Project structure separates concerns: config.py for settings, models.py for data structures, youtube.py/llm.py/obsidian.py for core functions, cli.py for interface
- Quality gates include ruff for linting, mypy for type checking, and pytest for testing with coverage reporting
- Configuration via .env file supports OpenRouter API key, Obsidian vault path, inbox folder, LLM model selection, and max transcript length
- Roadmap includes vault search, evaluation framework, MCP server implementation, and multi-source support for articles and PDFs

## Implications

- Demonstrates practical approach to building AI-powered knowledge management tools with proper engineering practices
- Architecture designed for easy migration to enterprise-grade systems while maintaining rapid iteration capability
- Provenance tracking enables audit trails and future re-processing with improved prompts as models evolve
- MCP-ready design positions the tool for integration with emerging AI workflow standards and tools like Open WebUI

## Source

[Original Source](file:///Users/larshansen/Documents/Dev/Coding/obsidian-ai-tools/README.md)
