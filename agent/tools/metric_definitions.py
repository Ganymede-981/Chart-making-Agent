METRIC_DICTIONARY: dict[str, str] = {
    "channel_metrics": "Table `channel_metrics` contains metrics across columns: `channels`, `facebook`, `instagram`, `linkedin`, `reels`, `shorts`, `x`, `youtube`, `threads`, `facebook_duration`, `instagram_duration`, `linkedin_duration`, `reels_duration`, `shorts_duration`, `x_duration`, `youtube_duration`, `threads_duration`",
    "input_type_metrics": "Table `input_type_metrics` contains metrics across columns: `input_type`, `uploaded_count`, `created_count`, `published_count`, `uploaded_duration`, `created_duration`, `published_duration`",
    "language_statistics": "Table `language_statistics` contains metrics across columns: `language`, `uploaded_count`, `created_count`, `published_count`, `uploaded_duration`, `created_duration`, `published_duration`",
    "monthly_counts_duration": "Table `monthly_counts_duration` contains metrics across columns: `month`, `total_uploaded`, `total_created`, `total_published`, `total_uploaded_duration`, `total_created_duration`, `total_published_duration`",
    "output_type_statistics": "Table `output_type_statistics` contains metrics across columns: `output_type`, `uploaded_count`, `created_count`, `published_count`, `uploaded_duration`, `created_duration`, `published_duration`",
    "video_list_data": "Table `video_list_data` contains metrics across columns: `headline`, `source`, `published`, `team_name`, `type`, `uploaded_by`, `video_id`, `published_platform`, `published_url`",
}


def retrieve_metric_definitions(search_term: str) -> str:
    """
    Look up metric table descriptions matching the given search term.

    Performs a simple substring match between ``search_term.lower()`` and
    the keys of ``METRIC_DICTIONARY``.  If no key matches, a generic fallback
    string listing all available domains is returned instead.

    Args:
        search_term: The user question or keyword to search against the
                     metric dictionary keys.

    Returns:
        Pipe-delimited string of matching metric descriptions, or a fallback
        message listing all available domain names.
    """
    results = [
        desc
        for term, desc in METRIC_DICTIONARY.items()
        if term in search_term.lower()
    ]
    if results:
        return " | ".join(results)

    return (
        "No specific metric matched exactly. Available domains are: "
        "channel_metrics, input_type_metrics, language_statistics, "
        "monthly_counts_duration, output_type_statistics, video_list_data. "
        "Use standard SQL counting/summing."
    )
