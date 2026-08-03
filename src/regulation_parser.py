"""RegulationParser: Regulation text parsing.

Parses regulation texts to extract structured information.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any

import structlog

logger = structlog.get_logger("regulation_parser")


@dataclass
class RegulationArticle:
    """A single regulation article."""
    number: str
    title: str
    content: str
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass
class Regulation:
    """A regulation document."""
    title: str
    articles: list[RegulationArticle] = field(default_factory=list)
    metadata: dict[str, Any] = field(default_factory=dict)
    
    def find_article(self, number: str) -> RegulationArticle | None:
        """Find article by number.
        
        Args:
            number: Article number
        
        Returns:
            Article or None
        """
        for article in self.articles:
            if article.number == number:
                return article
        return None
    
    def search_content(self, keyword: str) -> list[RegulationArticle]:
        """Search articles by keyword.
        
        Args:
            keyword: Search keyword
        
        Returns:
            List of matching articles
        """
        results = []
        for article in self.articles:
            if keyword in article.content or keyword in article.title:
                results.append(article)
        return results


class RegulationParser:
    """Parses regulation texts."""
    
    def __init__(self):
        self._regulations: list[Regulation] = []
    
    def parse(self, text: str, title: str = "") -> Regulation:
        """Parse regulation text.
        
        Args:
            text: Regulation text
            title: Regulation title
        
        Returns:
            Parsed regulation
        """
        articles = []
        
        # Parse articles (Chinese style: 第X条)
        article_pattern = r"第(\d+)条\s*[:：]\s*(.+?)(?=第\d+条|$)"
        for match in re.finditer(article_pattern, text, re.DOTALL):
            number = match.group(1)
            content = match.group(2).strip()
            
            # Extract title (first line)
            lines = content.split("\n")
            article_title = lines[0] if lines else ""
            
            articles.append(RegulationArticle(
                number=number,
                title=article_title,
                content=content,
            ))
        
        regulation = Regulation(
            title=title,
            articles=articles,
        )
        
        self._regulations.append(regulation)
        
        logger.info("regulation_parsed", title=title, articles=len(articles))
        
        return regulation
    
    def add_regulation(self, regulation: Regulation) -> None:
        """Add a regulation.
        
        Args:
            regulation: Regulation to add
        """
        self._regulations.append(regulation)
        logger.info("regulation_added", title=regulation.title)
    
    def search_regulations(self, keyword: str) -> list[RegulationArticle]:
        """Search all regulations for keyword.
        
        Args:
            keyword: Search keyword
        
        Returns:
            List of matching articles
        """
        results = []
        for regulation in self._regulations:
            results.extend(regulation.search_content(keyword))
        return results
    
    def get_regulation(self, title: str) -> Regulation | None:
        """Get regulation by title.
        
        Args:
            title: Regulation title
        
        Returns:
            Regulation or None
        """
        for regulation in self._regulations:
            if regulation.title == title:
                return regulation
        return None


def parse_regulation(text: str, title: str = "") -> Regulation:
    """Convenience function to parse regulation.
    
    Args:
        text: Regulation text
        title: Regulation title
    
    Returns:
        Parsed regulation
    """
    parser = RegulationParser()
    return parser.parse(text, title)
