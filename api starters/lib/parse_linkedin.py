#!/usr/bin/env python3
"""
Parse a LinkedIn Activity page clipboard scrape into a structured posts file.

Input:  raw markdown file with the LinkedIn paste content.
Output: {author-slug}-posts-{today}.md alongside the input, with
        post body, recency marker, reactions, comments, impressions.

Pure-stdlib. No OpenAI, no external APIs, no third-party deps.
"""

import re
from collections import Counter
from datetime import datetime
from pathlib import Path

def is_pure_repost(post_section, author_name=None):
    """
    RIGOROUS check if post is a pure repost with no original content.
    Returns True if this is ONLY reposted content, False if there's original commentary.
    """
    # PATTERN 1: "reposted this" keyword (existing logic)
    if 'reposted this' in post_section.lower():
        # Find the position of "reposted this"
        repost_idx = post_section.lower().find('reposted this')
        if repost_idx == -1:
            return False

        # Get text before "reposted this"
        text_before = post_section[:repost_idx].strip()

        # Remove ALL markdown artifacts, links, images, profile info
        text_before = re.sub(r'\[.*?\]\(.*?\)', '', text_before)  # Remove markdown links
        text_before = re.sub(r'!\[.*?\]\(.*?\)', '', text_before)  # Remove images
        text_before = re.sub(r'## Feed post number \d+', '', text_before, flags=re.IGNORECASE)
        text_before = re.sub(r'Promote this post.*?Boost.*?\]', '', text_before, flags=re.DOTALL)
        text_before = re.sub(r'View.*?graphic link', '', text_before, flags=re.IGNORECASE)
        text_before = re.sub(r'Photo of.*?', '', text_before, flags=re.IGNORECASE)
        text_before = re.sub(r'Activate to view larger image', '', text_before, flags=re.IGNORECASE)
        text_before = re.sub(r'Visible to anyone.*?', '', text_before)
        text_before = re.sub(r'CEO Coach.*?', '', text_before)  # Remove job titles/taglines
        text_before = re.sub(r'\d+x.*?Exits', '', text_before)  # Remove "3x Startup Exits" etc.
        text_before = re.sub(r'•', '', text_before)  # Remove bullet separators
        text_before = re.sub(r'\s+', ' ', text_before).strip()

        # If there's substantial original text (more than just formatting), it's not a pure repost
        if len(text_before) > 50:  # Threshold for original content
            return False

        return True

    # NOTE: "View X's graphic link" with short commentary is NOT detected here.
    # extract_original_post_copy (Pattern 0) handles that case via the structural
    # "second Visible to anyone" signal. Doing it here with a char-count threshold
    # is fragile: it drops legitimate short commentary AND misfires when the author
    # name is wrong (their own profile link gets treated as someone else's).

    # PATTERN 2: Congratulatory/endorsement patterns
    # "Yippee [Name]", "Congratulations [Name]", "Welcome [Name]"
    congrats_match = re.search(
        r'^(Yippee|Congratulations|Welcome|Excited for|So proud of)\s+([A-Z][a-z]+(?:\s+[A-Z][a-z]+)+)',
        post_section,
        re.MULTILINE | re.IGNORECASE
    )

    if congrats_match:
        congratulated_name = congrats_match.group(2)

        # If author_name provided and it's not them, this is likely a repost
        if author_name and congratulated_name.lower() != author_name.lower():
            # Check if followed by graphic link within 300 chars
            context_start = congrats_match.start()
            context = post_section[context_start:context_start + 300]
            if re.search(r"View.*?graphic link", context, re.IGNORECASE):
                return True

            # Even without graphic link, if very short (< 50 chars), likely a repost
            if len(post_section[context_start:].strip()) < 50:
                return True

    return False

def extract_original_post_copy(section, author_name=None):
    """
    RIGOROUSLY extract ONLY original author post copy.
    Filters out:
    - All UI artifacts
    - All reposted content (if any appears)
    - Profile images, links, markdown formatting artifacts
    - Engagement metrics embedded in text
    - Any non-author content
    """
    # Find text after the profile link and before engagement metrics
    # Look for text after the long profile link line that contains "Visible to anyone"
    profile_end = re.search(r'Visible to anyone.*?\n', section)
    if not profile_end:
        return None
    
    text_start = profile_end.end()

    # Check for reposted content indicators - these signal someone else's content
    # Search more chars to catch reposts that appear later in the post
    search_text = section[text_start:text_start + 4000]

    # Pattern 0: Second "Visible to anyone" = embedded/reposted content
    # Every post section has exactly ONE "Visible to anyone" (the author's own profile boundary).
    # Any ADDITIONAL "Visible to anyone" inside search_text signals embedded reposted content —
    # whether it's a person's post or a company/org page post.
    # This is the most deterministic pattern: it catches all repost types.
    second_visible = re.search(r'Visible to anyone', search_text)
    if second_visible:
        embedded_block_start = second_visible.start()
        # Prefer "View X graphic link" as the cut point (person reposts): it appears
        # immediately before the embedded person's profile block, after the author's commentary.
        # For company page reposts there is no "View X graphic link", so fall back to
        # the last blank line (\n\n) before the embedded block.
        graphic_link_before = re.search(r'View [^\n]+ graphic link', search_text[:embedded_block_start])
        if graphic_link_before:
            cut_pos = graphic_link_before.start()
        else:
            last_blank_pos = search_text.rfind('\n\n', 0, embedded_block_start)
            cut_pos = last_blank_pos if last_blank_pos >= 0 else embedded_block_start
        original_commentary = section[text_start:text_start + cut_pos].strip()
        return clean_post_copy(original_commentary, author_name)

    # Pattern 1: Someone else's name repeated (e.g., "Vincent VazquezVincent Vazquez")
    # This is a clear indicator of reposted content
    repeated_name = re.search(r'^([A-Z][a-z]+ [A-Z][a-z]+)\1', search_text, re.MULTILINE)
    if repeated_name:
        # Found reposted content - extract only what comes before it
        repost_start = repeated_name.start()
        original_commentary = section[text_start:text_start + repost_start].strip()
        return clean_post_copy(original_commentary, author_name)
    
    # Pattern 2: Another person's profile info with connection degree (2nd, 3rd+, Verified, Premium)
    # Look for name followed by connection indicators on same or next line
    repost_pattern = re.search(r'^([A-Z][a-z]+ [A-Z][a-z]+).*?(?:\n.*?)?(?:•\s*\d+nd|•\s*\d+rd\+|•\s*Verified|•\s*Premium)', search_text, re.MULTILINE | re.DOTALL)
    if repost_pattern:
        # Found reposted content - extract only what comes before it
        repost_start = repost_pattern.start()
        original_commentary = section[text_start:text_start + repost_start].strip()
        return clean_post_copy(original_commentary, author_name)
    
    # Pattern 3: Standalone name on its own line (likely reposted content)
    # If we have author name, check if this is a different person
    if author_name:
        # Extract potential names (First Last pattern)
        standalone_names = re.findall(r'^([A-Z][a-z]+ [A-Z][a-z]+)$', search_text, re.MULTILINE)
        for name in standalone_names:
            # If it's not the author and appears alone, it's likely reposted content
            if name.lower() != author_name.lower():
                # Check if it appears near connection indicators
                name_pos = search_text.find(name)
                if name_pos != -1:
                    # Look ahead for connection indicators
                    context = search_text[name_pos:name_pos + 500]
                    if re.search(r'(?:•\s*\d+nd|•\s*\d+rd\+|•\s*Verified|•\s*Premium|Safety Officer|Project Manager|USMC)', context, re.IGNORECASE):
                        # This is reposted content - stop before it
                        repost_start = name_pos
                        original_commentary = section[text_start:text_start + repost_start].strip()
                        return clean_post_copy(original_commentary, author_name)
    
    # Pattern 4: "View [Other Person]'s graphic link" (someone else's profile)
    # Use a broad pattern — don't rely on apostrophe character (varies by clipboard encoding)
    secondary_profile = re.search(r"View [^\n]+ graphic link", search_text)
    if secondary_profile:
        # This post has reposted content - extract ONLY original commentary, stop at repost
        # Find where the repost starts (at the secondary profile)
        repost_start = secondary_profile.start()
        original_commentary = section[text_start:text_start + repost_start].strip()
        return clean_post_copy(original_commentary, author_name)
    
    # No repost detected by structural patterns - extract the full original content
    # Extract text until we hit engagement metrics, images, or action buttons
    text_end_patterns = [
        r'(- \!\[like)',                                           # Engagement icons (markdown)
        r'(\[.*?impressions)',                                     # Impressions link
        r'(\nLike\n\nComment\n)',                                  # Full action button sequence
        r'(\nlike(?:love|celebrate|insightful|funny|support)+\n)', # Concatenated reaction labels
        r'(\nPhoto of [A-Z][a-z]+ [A-Z])',                        # Profile photo with full name
        r'(\[.*?View analytics.*?\])',                             # Analytics link
    ]

    text_end_pos = len(section)
    for pattern in text_end_patterns:
        match = re.search(pattern, section[text_start:], re.MULTILINE)
        if match:
            text_end_pos = min(text_end_pos, match.start())

    post_copy = section[text_start:text_start + text_end_pos].strip()
    return clean_post_copy(post_copy, author_name)

def clean_post_copy(post_copy, author_name=None):
    """
    RIGOROUSLY clean post copy - remove ALL UI artifacts and non-author content.
    """
    if not post_copy:
        return None
    
    # Remove "Follow" UI element (standalone on its own line)
    post_copy = re.sub(r'^Follow\s*$', '', post_copy, flags=re.MULTILINE)
    post_copy = re.sub(r'^\s*Follow\s*$', '', post_copy, flags=re.MULTILINE)
    
    # Remove "…more" indicators
    post_copy = re.sub(r'…more\s*$', '', post_copy, flags=re.MULTILINE)
    
    # Remove ALL image markdown
    post_copy = re.sub(r'!\[.*?\]\(.*?\)', '', post_copy)
    
    # Remove image activation text AND the alt-text description line that follows it
    post_copy = re.sub(r'Activate to view larger image,?\s*\n[^\n]*', '', post_copy, flags=re.IGNORECASE)
    post_copy = re.sub(r'Activate to view larger image,?\s*', '', post_copy, flags=re.IGNORECASE)  # leftovers without newline
    post_copy = re.sub(r'No alternative text description for this image\s*', '', post_copy, flags=re.IGNORECASE)

    # Remove "Show translation" UI button
    post_copy = re.sub(r'^Show translation\s*$', '', post_copy, flags=re.MULTILINE)

    # Remove "Photo of [Name]" artifacts
    post_copy = re.sub(r'Photo of [A-Z][a-z]+.*?\n', '', post_copy)

    # === NEW: Remove LinkedIn UX artifacts ===
    # Pattern 1: "hashtag#tag" → "#tag"
    # This is LinkedIn's internal representation that appears in scrapes
    post_copy = re.sub(r'\bhashtag#', '#', post_copy)

    # Pattern 2: Standalone UI labels (LinkedIn UI artifact)
    # These appear as standalone words on their own line
    ui_labels = ['text', 'image', 'video', 'link', 'document', 'poll', 'article']
    for label in ui_labels:
        # Only remove if it's alone on a line (not part of real content)
        post_copy = re.sub(rf'^\s*{label}\s*$', '', post_copy, flags=re.MULTILINE | re.IGNORECASE)

    # Pattern 3: "View X's graphic link" lines - REMOVE completely
    # But check author_name to preserve author's own links (if any)
    # Remove all "View X's graphic link" lines regardless of apostrophe character variant
    post_copy = re.sub(
        r"View [^\n]+? graphic link[^\n]*(\n|$)",
        '',
        post_copy,
        flags=re.IGNORECASE
    )

    # Pattern 4: Congratulatory headers for other people
    # "Yippee [Name]" should be removed if Name != author
    if author_name:
        def remove_non_author_congrats(match):
            congratulated_name = match.group(2)
            if congratulated_name.lower() != author_name.lower():
                return ''  # Remove if congratulating someone else
            return match.group(0)  # Keep if congratulating self (rare but possible)

        post_copy = re.sub(
            r'^(Yippee|Congratulations|Welcome|Excited for|So proud of)\s+([A-Z][a-z]+(?:\s+[A-Z][a-z]+)+).*?(?:\n|$)',
            remove_non_author_congrats,
            post_copy,
            flags=re.MULTILINE | re.IGNORECASE
        )
    # === END NEW SECTION ===

    # Remove reposted content indicators - look for patterns indicating someone else's profile
    # Pattern 1: Name repeated (e.g., "Vincent VazquezVincent Vazquez")
    post_copy = re.sub(r'^([A-Z][a-z]+ [A-Z][a-z]+)\1.*?\n', '', post_copy, flags=re.MULTILINE)
    
    # Pattern 2: Standalone names that aren't the author (likely reposted content)
    if author_name:
        # Remove standalone "First Last" names on their own line that aren't the author
        def remove_non_author_name(match):
            name = match.group(0).strip()
            # If it's not the author, remove it
            if name.lower() != author_name.lower():
                return ''
            return name
        
        post_copy = re.sub(r'^([A-Z][a-z]+ [A-Z][a-z]+)$', remove_non_author_name, post_copy, flags=re.MULTILINE)
    
    # Pattern 3: Connection degree indicators (2nd, 3rd+, Verified, Premium) - these indicate reposts
    post_copy = re.sub(r'.*?•\s*\d+nd.*?\n', '', post_copy, flags=re.MULTILINE | re.IGNORECASE)
    post_copy = re.sub(r'.*?•\s*\d+rd\+.*?\n', '', post_copy, flags=re.MULTILINE | re.IGNORECASE)
    post_copy = re.sub(r'.*?•\s*Verified.*?\n', '', post_copy, flags=re.MULTILINE | re.IGNORECASE)
    post_copy = re.sub(r'.*?•\s*Premium.*?\n', '', post_copy, flags=re.MULTILINE | re.IGNORECASE)
    
    # Pattern 4: Profile titles that indicate reposted content
    post_copy = re.sub(r'.*?(?:Safety Officer|Project Manager|USMC Veteran).*?\n', '', post_copy, flags=re.MULTILINE | re.IGNORECASE)
    
    # Remove company/org page profile artifacts (from embedded company reposts that slipped through)
    # Doubled company name: "ProtoProto", "ADWEEKADWEEK", "ON_DiscourseON_Discourse"
    post_copy = re.sub(r'^(\S+)\1\s*$', '', post_copy, flags=re.MULTILINE)
    # Doubled follower count: "3,513 followers3,513 followers"
    post_copy = re.sub(r'^\d[\d,]+ followers\d[\d,]+ followers\s*$', '', post_copy, flags=re.MULTILINE)
    # Doubled follower count variant without comma: "1452146 followers1452146 followers"
    post_copy = re.sub(r'^\d+ followers\d+ followers\s*$', '', post_copy, flags=re.MULTILINE)

    # Remove "Your document has finished loading" LinkedIn artifact
    post_copy = re.sub(r'Your document has finished loading\s*', '', post_copy, flags=re.IGNORECASE)

    # Remove engagement artifacts - these appear at the end after the actual content
    post_copy = re.sub(r'\n(?:like|insightful|celebrate|love|support|funny)+\n\d+.*', '', post_copy, flags=re.IGNORECASE | re.DOTALL)
    post_copy = re.sub(r'You and \d+ others?\s*', '', post_copy)
    post_copy = re.sub(r'[A-Z][a-z]+ and \d+ others?\s*', '', post_copy)
    
    # Remove profile link artifacts that might appear in text
    post_copy = re.sub(r'\[.*?\]\(https://www\.linkedin\.com/.*?\)', '', post_copy)
    
    # Remove Boost/Promote UI artifacts
    post_copy = re.sub(r'Promote this post.*?', '', post_copy, flags=re.DOTALL | re.IGNORECASE)
    post_copy = re.sub(r'Boost.*?', '', post_copy, flags=re.DOTALL | re.IGNORECASE)

    # === Strip LinkedIn-generated card text ===
    # These are machine-generated by LinkedIn and follow consistent formats.
    # Cards always appear AFTER the author's text, so we find the first card
    # pattern and strip from that point to end.

    card_patterns = [
        # Credential verification cards: "X was issued by Y to Z."
        r'\n.+(?:was issued|has been issued) by .+ to .+',
        # Announcement cards
        r'\nStarting a New Position\s*$',
        # Job listing cards
        r'\n.+\nJob by .+',
    ]

    earliest_card_pos = len(post_copy)
    for pattern in card_patterns:
        match = re.search(pattern, post_copy, re.MULTILINE)
        if match:
            earliest_card_pos = min(earliest_card_pos, match.start())

    # Link preview cards: bare domain line (e.g., "acquired.fm", "wearepro.to", "geldinstitute.de")
    # Any TLD covered. The article title line immediately before it is also card text.
    domain_match = re.search(r'\n[a-z][a-z0-9.-]*\.[a-z]{2,6}\s*$', post_copy, re.MULTILINE)
    if domain_match:
        # Walk back to include the article title line above the domain
        title_start = post_copy.rfind('\n', 0, domain_match.start())
        if title_start >= 0:
            earliest_card_pos = min(earliest_card_pos, title_start)
        else:
            earliest_card_pos = min(earliest_card_pos, domain_match.start())

    # Link preview cards: [Article title (25+ chars)]\n[Company or Author name (1-3 Title Case words)]
    # This catches cards where the source is a brand/person name, not a domain.
    # Requires: title is long enough to be an article headline; source is pure Title Case words.
    title_source_match = re.search(
        r'\n([^\n]{25,})\n([A-Z][a-z]+(?:\s+[A-Z][a-z]+){0,2})\s*$',
        post_copy
    )
    if title_source_match:
        source_words = title_source_match.group(2).split()
        # Source must be all-alpha Title Case words (company name or person name, no punctuation)
        if all(w[0].isupper() and w.isalpha() for w in source_words):
            earliest_card_pos = min(earliest_card_pos, title_source_match.start())

    if earliest_card_pos < len(post_copy):
        post_copy = post_copy[:earliest_card_pos]
    # === End card text removal ===

    # Normalize whitespace but preserve paragraph breaks
    post_copy = re.sub(r'[ \t]+', ' ', post_copy)  # Multiple spaces to single
    post_copy = re.sub(r'\n\s*\n\s*\n+', '\n\n', post_copy)  # Multiple newlines to double max
    post_copy = post_copy.strip()
    
    # Final validation: if what's left is too short or looks like UI artifact, reject it
    if len(post_copy) < 10:
        return None
    
    # Reject if it looks like only UI text (e.g., "View Greg Rublev's graphic link")
    if re.match(r'^View.*?graphic link', post_copy, re.IGNORECASE):
        return None
    
    return post_copy

def extract_recency(section):
    """Extract recency text (e.g., '1d', '2w', '3mo')."""
    recency_match = re.search(r'(\d+[dwmoyr]+|now|Just now)\s*•', section, re.IGNORECASE)
    if recency_match:
        return recency_match.group(1)
    return None

def extract_impressions(section):
    """Extract impressions count."""
    # Try markdown format first: **241 impressions**
    impressions_match = re.search(r'\*\*([\d,]+)\s*impressions\*\*', section)
    if impressions_match:
        return int(impressions_match.group(1).replace(',', ''))
    
    # Try plain text format: 241 impressions
    impressions_match = re.search(r'([\d,]+)\s+impressions', section)
    if impressions_match:
        return int(impressions_match.group(1).replace(',', ''))
    
    return None

def extract_reactions(section):
    """Extract reactions count."""
    # Pattern 0: Bulleted format from current LinkedIn UI (post-Apr 2026 paste format).
    # Looks like:
    #    * 31
    #    *
    #    * 2 comments
    #    * 1 repost
    # Match: a line that is whitespace + "*" + whitespace + digits + EOL,
    # where the digits stand alone (not followed by "comments"/"reposts"/etc.).
    reaction_match = re.search(r'^\s*\*\s+(\d+)\s*$', section, re.MULTILINE)
    if reaction_match:
        return int(reaction_match.group(1))

    # Pattern 1: Markdown format "- ![like]...26Kristian Hanelt and 25 others"
    reaction_match = re.search(r'- \!\[like\].*?(\d+)(?:[A-Za-z]|You).*?(?:and \d+ others|$)', section)
    if reaction_match:
        return int(reaction_match.group(1))
    
    # Pattern 2: Markdown format "- ![like]...2" (just a number)
    reaction_match = re.search(r'- \!\[like\].*?(\d+)\s*$', section, re.MULTILINE)
    if reaction_match:
        return int(reaction_match.group(1))
    
    # Pattern 3: Plain text clipboard format "like\n5\nYou and 4 others"
    reaction_match = re.search(r'(?:^|\n)like[a-z]*\s*\n(\d+)\s*\n(?:You and \d+ others|[A-Z][a-z]+ and \d+ others|\w+)', section, re.MULTILINE | re.IGNORECASE)
    if reaction_match:
        return int(reaction_match.group(1))
    
    # Pattern 4: Fallback - number followed by name or "You" in reaction context
    reaction_match = re.search(r'(\d+)(?:[A-Z][a-z]+|You)(?:\s+and\s+\d+\s+others)?', section)
    if reaction_match:
        return int(reaction_match.group(1))
    
    return None

def extract_comments(section):
    """Extract comments count."""
    # Try markdown format first: "- - 2 comments"
    comments_match = re.search(r'- - (\d+)\s*comments?', section)
    if comments_match:
        return int(comments_match.group(1))

    # Try plain text format: "2 comments"
    comments_match = re.search(r'^(\d+)\s+comments?$', section, re.MULTILINE)
    if comments_match:
        return int(comments_match.group(1))

    # Bulleted format from current LinkedIn UI (post-Apr 2026): "   * 2 comments"
    comments_match = re.search(r'^\s*\*\s+(\d+)\s+comments?\s*$', section, re.MULTILINE)
    if comments_match:
        return int(comments_match.group(1))

    return None

def parse_posts(md_file_path, author_name=None):
    """
    Parse markdown file and extract post data.
    Only extracts original author content - filters out pure reposts.
    """
    # Read as UTF-8 (clipboard data from LinkedIn should always be UTF-8)
    with open(md_file_path, 'r', encoding='utf-8') as f:
        content = f.read()

    # Split into post sections - handle multiple markdown formats:
    # 1. "## Feed post number" (heading format)
    # 2. "Feed post number" (plain text clipboard format)
    # 3. "- ## Feed post number" (list item format from some exports)
    post_pattern = r'^-?\s*#{0,2}\s*Feed post number \d+'
    post_sections = re.split(post_pattern, content, flags=re.MULTILINE)

    posts = []

    for i, section in enumerate(post_sections[1:], 1):  # Skip first section (header)
        # Layer 1: Skip pure reposts - NO original content
        if is_pure_repost(section, author_name):
            continue

        # Extract original post copy (rigorously filtered)
        post_copy = extract_original_post_copy(section, author_name)
        if not post_copy or len(post_copy.strip()) < 10:
            # Skip posts with no substantial original content
            continue

        # === Layer 3: Final validation - Does cleaned output still contain repost indicators? ===

        # Check for "View X's graphic link" that slipped through (apostrophe-agnostic)
        if re.search(r"View [^\n]+ graphic link", post_copy, re.IGNORECASE):
            # This post still has repost indicators after cleaning - skip it
            continue

        # Check for standalone non-author names (potential missed reposts)
        if author_name:
            standalone_names = re.findall(r'^([A-Z][a-z]+\s+[A-Z][a-z]+)$', post_copy, re.MULTILINE)
            has_other_names = any(name.lower() != author_name.lower() for name in standalone_names)
            if has_other_names and len(post_copy) < 150:
                # Very short post with someone else's name - likely missed repost
                continue

        # === END Layer 3 validation ===

        post_data = {
            'recency': extract_recency(section),
            'post_copy': post_copy,
            'impressions': extract_impressions(section),
            'reactions': extract_reactions(section),
            'comments': extract_comments(section),
        }

        posts.append(post_data)
    
    return posts

def calculate_stats(posts):
    """Calculate average stats for the header."""
    total_posts = len(posts)
    
    impressions_list = [p['impressions'] for p in posts if p['impressions'] is not None]
    avg_impressions = sum(impressions_list) / len(impressions_list) if impressions_list else None
    
    reactions_list = [p['reactions'] for p in posts if p['reactions'] is not None]
    avg_reactions = sum(reactions_list) / len(reactions_list) if reactions_list else None
    
    comments_list = [p['comments'] for p in posts if p['comments'] is not None]
    avg_comments = sum(comments_list) / len(comments_list) if comments_list else None
    
    return {
        'total_posts': total_posts,
        'avg_impressions': avg_impressions,
        'avg_reactions': avg_reactions,
        'avg_comments': avg_comments,
    }

def format_stat(value, is_int=False):
    """Format a stat value for display."""
    if value is None:
        return "N/A"
    if is_int:
        return str(int(value))
    return f"{value:.1f}"

def export_posts_to_markdown(posts, output_path, author_name, stats):
    """Export posts to a single markdown file with header stats and post details."""
    lines = []
    
    # Header with author name
    lines.append(f"# {author_name}")
    lines.append("")
    
    # Stats section
    lines.append(f"- Number of posts: {stats['total_posts']}")
    lines.append(f"- Average impressions per post: {format_stat(stats['avg_impressions'])}")
    lines.append(f"- Average reactions per post: {format_stat(stats['avg_reactions'])}")
    lines.append(f"- Average comments per post: {format_stat(stats['avg_comments'])}")
    lines.append("")
    lines.append("---")
    lines.append("")
    
    # Each post
    for post in posts:
        # Recency
        if post['recency']:
            lines.append(f"[{post['recency']}]")
        else:
            lines.append("[unknown]")
        lines.append("")
        
        # Post copy
        lines.append(post['post_copy'])
        lines.append("")
        
        # Metrics (only show if available, with labels)
        if post['impressions'] is not None:
            lines.append(f"Impressions: {post['impressions']}")
        if post['reactions'] is not None:
            lines.append(f"Reactions: {post['reactions']}")
        if post['comments'] is not None:
            lines.append(f"Comments: {post['comments']}")
        
        lines.append("")
        lines.append("---")
        lines.append("")

    # Write to file
    with open(output_path, 'w', encoding='utf-8') as f:
        f.write('\n'.join(lines))

    print(f"Exported {len(posts)} posts to {output_path}")

def extract_author_name_from_first_post(content):
    """
    Extract author name by scanning ALL posts and finding the most-frequently doubled name.

    LinkedIn doubles every name in its profile blocks: "Peter PawlickPeter Pawlick".
    The primary author appears once per post they wrote — always the most common
    doubled name in the scrape.  Looking only at post 1 fails when post 1 is a repost
    (the reposted person's doubled name appears first instead of the author's).
    """
    # A name component covers: "Barry", "H." (initial), "O'Brien", "Case-Hall".
    name_component = r"[A-Z][A-Za-z']*\.?"
    name_word = rf"(?:{name_component})(?:-{name_component})*"
    full_name = rf"(?:{name_word})(?:\s+{name_word})+"   # requires at least two words

    # Collect all doubled names across the entire scrape
    # Format 1: Markdown link bracket  [NameName • ...]
    md_pattern = rf'\[({full_name})\1\s*[•\[]'
    # Format 2: Plain text — doubled name as a complete line (no brackets)
    bare_pattern = rf'^({full_name})\1\s*$'

    all_names = re.findall(md_pattern, content) + re.findall(bare_pattern, content, re.MULTILINE)

    if all_names:
        # Most common doubled name = the primary author
        normalized = [' '.join(n.split()) for n in all_names]
        return Counter(normalized).most_common(1)[0][0]

    # Fallback: first "View [Name]'s graphic link" in the file
    view_match = re.search(r"View ([A-Z][^\n]+?) graphic link", content)
    if view_match:
        candidate = re.sub(r"['\u2019]s\s*$", '', view_match.group(1).strip())
        if len(candidate.split()) >= 2:
            return candidate

    return None

def extract_author_name(content):
    """Extract the author's name from the LinkedIn scrape using deterministic regex."""
    author_name = extract_author_name_from_first_post(content)
    return author_name if author_name else "Unknown Author"

def main():
    import sys

    if len(sys.argv) != 2:
        print("Usage: python3 parse_linkedin.py <path-to-raw-scrape.md>")
        sys.exit(2)

    md_file = Path(sys.argv[1])
    if not md_file.exists():
        print(f"Error: file not found: {md_file}")
        sys.exit(2)

    content = md_file.read_text(encoding='utf-8')
    author_name = extract_author_name(content)
    print(f"Detected author: {author_name}")

    posts = parse_posts(md_file, author_name)
    print(f"Found {len(posts)} posts (pure reposts filtered out)")

    stats = calculate_stats(posts)
    today = datetime.now().strftime('%Y-%m-%d')
    author_slug = author_name.replace(' ', '-').lower()
    output_file = md_file.parent / f"{author_slug}-posts-{today}.md"

    export_posts_to_markdown(posts, output_file, author_name, stats)
    print(f"\nDone! Output saved to: {output_file}")

if __name__ == '__main__':
    main()
