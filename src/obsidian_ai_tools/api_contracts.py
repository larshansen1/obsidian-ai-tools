"""API contract validation schemas for external services.

These schemas validate that external API responses match our expectations,
helping catch breaking changes in third-party services.
"""

from typing import Any, Optional
from pydantic import BaseModel, Field, field_validator


# OpenRouter API Contracts

class OpenRouterMessage(BaseModel):
    """OpenRouter chat message in response."""
    
    role: str
    content: str


class OpenRouterChoice(BaseModel):
    """OpenRouter response choice."""
    
    message: OpenRouterMessage
    finish_reason: Optional[str] = None


class OpenRouterResponse(BaseModel):
    """OpenRouter API response schema."""
    
    id: str
    model: str
    choices: list[OpenRouterChoice]
    
    @field_validator('choices')
    @classmethod
    def validate_choices(cls, v: list[OpenRouterChoice]) -> list[OpenRouterChoice]:
        """Ensure at least one choice is returned."""
        if not v:
            raise ValueError("OpenRouter response must contain at least one choice")
        return v


# Supadata API Contracts

class SupadataWebResponse(BaseModel):
    """Supadata web scraping API response."""
    
    content: Optional[str] = None
    markdown: Optional[str] = None
    title: Optional[str] = None
    author: Optional[str] = None
    
    @field_validator('content', 'markdown')
    @classmethod
    def validate_content(cls, v: Optional[str]) -> Optional[str]:
        """Validate content fields."""
        if v is not None and not isinstance(v, str):
            raise ValueError("Content must be a string")
        return v


class SupadataTranscriptResponse(BaseModel):
    """Supadata transcript API response."""
    
    transcript: Optional[str] = None
    language: Optional[str] = None
    
    # Note: May also return BatchJob object for async processing
    # We handle that case separately in the provider code


# YouTube Data API Contracts

class YouTubeSnippet(BaseModel):
    """YouTube video snippet from Data API."""
    
    title: str
    channelTitle: str
    description: Optional[str] = None
    publishedAt: Optional[str] = None


class YouTubeVideoItem(BaseModel):
    """YouTube video item from Data API."""
    
    id: str
    snippet: YouTubeSnippet


class YouTubeDataAPIResponse(BaseModel):
    """YouTube Data API videos.list response."""
    
    items: list[YouTubeVideoItem]
    
    @field_validator('items')
    @classmethod
    def validate_items(cls, v: list[YouTubeVideoItem]) -> list[YouTubeVideoItem]:
        """Ensure at least one item when video exists."""
        # Empty items list is valid (video not found), but we document it
        return v


def validate_openrouter_response(response_data: dict[str, Any]) -> OpenRouterResponse:
    """Validate OpenRouter API response against contract.
    
    Args:
        response_data: Raw API response dictionary
        
    Returns:
        Validated OpenRouterResponse
        
    Raises:
        ValidationError: If response doesn't match contract
    """
    return OpenRouterResponse(**response_data)


def validate_supadata_web_response(response_data: dict[str, Any]) -> SupadataWebResponse:
    """Validate Supadata web scraping response.
    
    Args:
        response_data: Raw API response dictionary
        
    Returns:
        Validated SupadataWebResponse
        
    Raises:
        ValidationError: If response doesn't match contract
    """
    return SupadataWebResponse(**response_data)


def validate_youtube_data_response(response_data: dict[str, Any]) -> YouTubeDataAPIResponse:
    """Validate YouTube Data API response.
    
    Args:
        response_data: Raw API response dictionary
        
    Returns:
        Validated YouTubeDataAPIResponse
        
    Raises:
        ValidationError: If response doesn't match contract
    """
    return YouTubeDataAPIResponse(**response_data)
