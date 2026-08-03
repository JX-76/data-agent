"""Regulation Integration: Regulation parsing integration with DAG.

Integrates RegulationParser with the DAG execution.
"""

from __future__ import annotations

from typing import Any

from regulation_parser import RegulationParser, Regulation, RegulationArticle


class RegulationIntegration:
    """Integrates regulation parsing with DAG execution."""
    
    def __init__(self):
        self.parser = RegulationParser()
    
    def parse(self, text: str, title: str = "") -> Regulation:
        """Parse regulation text.
        
        Args:
            text: Regulation text
            title: Regulation title
        
        Returns:
            Parsed regulation
        """
        return self.parser.parse(text, title)
    
    def search_regulations(self, keyword: str) -> list[RegulationArticle]:
        """Search all regulations for keyword.
        
        Args:
            keyword: Search keyword
        
        Returns:
            List of matching articles
        """
        return self.parser.search_regulations(keyword)
    
    def get_regulation(self, title: str) -> Regulation | None:
        """Get regulation by title.
        
        Args:
            title: Regulation title
        
        Returns:
            Regulation or None
        """
        return self.parser.get_regulation(title)
    
    def add_regulation(self, regulation: Regulation) -> None:
        """Add a regulation.
        
        Args:
            regulation: Regulation to add
        """
        self.parser.add_regulation(regulation)


def create_regulation_integration() -> RegulationIntegration:
    """Convenience function to create regulation integration.
    
    Returns:
        Regulation integration
    """
    return RegulationIntegration()
