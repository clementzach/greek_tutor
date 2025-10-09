"""
Spaced Repetition System (SM-2 Algorithm)
Similar to Anki's algorithm for scheduling flashcard reviews.
"""
from datetime import datetime, timedelta
from typing import Dict, Any


def calculate_next_review(
    quality: int,
    ease_factor: float = 2.5,
    interval_days: float = 0,
    times_reviewed: int = 0
) -> Dict[str, Any]:
    """
    Calculate next review date and updated parameters based on SM-2 algorithm.

    Args:
        quality: Rating from 0-5 where:
            0 = complete blackout
            1 = incorrect, but recognized
            2 = incorrect, but easy to recall correct answer
            3 = correct, but difficult recall
            4 = correct, after some hesitation
            5 = perfect recall
        ease_factor: Current ease factor (default 2.5)
        interval_days: Current interval in days (default 0)
        times_reviewed: Number of times reviewed (default 0)

    Returns:
        Dict with: ease_factor, interval_days, next_review_date
    """
    # Update ease factor based on quality
    new_ease = ease_factor + (0.1 - (5 - quality) * (0.08 + (5 - quality) * 0.02))
    new_ease = max(1.3, new_ease)  # Minimum ease factor is 1.3

    # Calculate new interval
    if quality < 3:
        # Failed recall - reset to beginning
        new_interval = 0
        repetition = 0
    else:
        # Successful recall
        if times_reviewed == 0:
            new_interval = 1
        elif times_reviewed == 1:
            new_interval = 6
        else:
            new_interval = interval_days * new_ease
        repetition = times_reviewed + 1

    # Calculate next review date
    next_date = datetime.utcnow() + timedelta(days=new_interval)

    return {
        'ease_factor': round(new_ease, 2),
        'interval_days': round(new_interval, 2),
        'next_review_date': next_date.isoformat(),
        'times_reviewed': repetition
    }


def quality_from_verdict(verdict: str) -> int:
    """
    Convert quiz verdict to SM-2 quality rating (0-5).

    Args:
        verdict: 'correct', 'partial', or 'incorrect'

    Returns:
        Quality rating 0-5
    """
    mapping = {
        'correct': 5,      # Perfect recall
        'partial': 3,      # Correct with difficulty
        'incorrect': 1     # Incorrect but recognized
    }
    return mapping.get(verdict.lower(), 1)


def mastery_from_ease_and_interval(ease_factor: float, interval_days: float) -> float:
    """
    Calculate a mastery score (0-1) from ease factor and interval.
    Higher ease and longer intervals = higher mastery.

    Args:
        ease_factor: Current ease factor
        interval_days: Current interval in days

    Returns:
        Mastery score between 0 and 1
    """
    # Normalize ease factor (1.3 to 3.5 range maps to 0-0.5)
    ease_score = min(0.5, (ease_factor - 1.3) / 4.4)

    # Normalize interval (0 to 180 days maps to 0-0.5)
    interval_score = min(0.5, interval_days / 360)

    return round(ease_score + interval_score, 2)
