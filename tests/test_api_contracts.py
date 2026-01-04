"""Tests for API contract validation schemas."""

import pytest
from pydantic import ValidationError

from obsidian_ai_tools.api_contracts import (
    OpenRouterResponse,
    SupadataWebResponse,
    YouTubeDataAPIResponse,
    validate_openrouter_response,
    validate_supadata_web_response,
    validate_youtube_data_response,
)


class TestOpenRouterContract:
    """Test OpenRouter API contract validation."""

    def test_valid_openrouter_response(self):
        """Test validation of valid OpenRouter response."""
        response = {
            "id": "gen-123",
            "model": "anthropic/claude-3.5-sonnet",
            "choices": [
                {
                    "message": {
                        "role": "assistant",
                        "content": '{"title": "Test", "tags": ["test"]}'
                    },
                    "finish_reason": "stop"
                }
            ]
        }
        
        validated = validate_openrouter_response(response)
        assert validated.id == "gen-123"
        assert len(validated.choices) == 1
        assert validated.choices[0].message.content.startswith("{")

    def test_openrouter_response_missing_choices(self):
        """Test that missing choices raises validation error."""
        response = {
            "id": "gen-123",
            "model": "anthropic/claude-3.5-sonnet",
            "choices": []
        }
        
        with pytest.raises(ValidationError, match="at least one choice"):
            validate_openrouter_response(response)

    def test_openrouter_response_missing_required_fields(self):
        """Test that missing required fields raises error."""
        response = {
            "id": "gen-123",
            # Missing model
            "choices": [{"message": {"role": "assistant", "content": "test"}}]
        }
        
        with pytest.raises(ValidationError):
            validate_openrouter_response(response)


class TestSupadataContract:
    """Test Supadata API contract validation."""

    def test_valid_supadata_web_response(self):
        """Test validation of valid Supadata response."""
        response = {
            "content": "Article content here",
            "markdown": "# Article\n\nContent",
            "title": "Test Article",
            "author": "Test Author"
        }
        
        validated = validate_supadata_web_response(response)
        assert validated.content == "Article content here"
        assert validated.title == "Test Article"

    def test_supadata_response_optional_fields(self):
        """Test Supadata response with optional fields missing."""
        response = {
            "content": "Minimal content"
        }
        
        validated = validate_supadata_web_response(response)
        assert validated.content == "Minimal content"
        assert validated.title is None
        assert validated.author is None

    def test_supadata_response_empty(self):
        """Test Supadata response with all fields None."""
        response = {}
        
        validated = validate_supadata_web_response(response)
        assert validated.content is None
        assert validated.markdown is None


class TestYouTubeDataAPIContract:
    """Test YouTube Data API contract validation."""

    def test_valid_youtube_data_response(self):
        """Test validation of valid YouTube API response."""
        response = {
            "items": [
                {
                    "id": "test123",
                    "snippet": {
                        "title": "Test Video",
                        "channelTitle": "Test Channel",
                        "description": "Test description"
                    }
                }
            ]
        }
        
        validated = validate_youtube_data_response(response)
        assert len(validated.items) == 1
        assert validated.items[0].snippet.title == "Test Video"

    def test_youtube_data_empty_items(self):
        """Test YouTube API response with no items (video not found)."""
        response = {"items": []}
        
        validated = validate_youtube_data_response(response)
        assert len(validated.items) == 0

    def test_youtube_data_missing_required_snippet_fields(self):
        """Test that missing required snippet fields raises error."""
        response = {
            "items": [
                {
                    "id": "test123",
                    "snippet": {
                        "title": "Test Video"
                        # Missing channelTitle
                    }
                }
            ]
        }
        
        with pytest.raises(ValidationError):
            validate_youtube_data_response(response)


class TestContractBreakingChanges:
    """Test detection of breaking changes in API responses."""

    def test_openrouter_changed_response_structure(self):
        """Test detection when OpenRouter changes response structure."""
        # Simulate API returning different structure
        response = {
            "request_id": "123",  # Changed from 'id'
            "model": "test",
            "choices": [{"message": {"role": "assistant", "content": "test"}}]
        }
        
        with pytest.raises(ValidationError):
            validate_openrouter_response(response)

    def test_supadata_unexpected_type(self):
        """Test detection when Supadata returns unexpected type."""
        response = {
            "content": 123  # Should be string, not int
        }
        
        with pytest.raises(ValidationError):
            validate_supadata_web_response(response)

    def test_youtube_missing_items_key(self):
        """Test detection when YouTube API response missing items."""
        response = {
            "kind": "youtube#videoListResponse"
            # Missing 'items' key
        }
        
        with pytest.raises(ValidationError):
            validate_youtube_data_response(response)
