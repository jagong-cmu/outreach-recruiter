"""Synthetic candidates for testing the pipeline without scraping anything.

These are fictional. They let you exercise scoring, drafting, and the dashboard
immediately — and they're a useful reference for the candidate dict shape that
every data source must produce.
"""

SAMPLE_CANDIDATES = [
    {
        "name": "Maya Rodriguez",
        "headline": "Incoming CMU '30 | Key Club President | Social Media Lead",
        "location": "Austin, TX",
        "profile_url": "https://example.com/in/maya-rodriguez",
        "email": "maya.sample@example.com",
        "grad_year": 2030,
        "school": "Carnegie Mellon University",
        "source": "sample",
        "raw_text": (
            "President of Key Club, organized community service events and a "
            "donation drive. Ran the club Instagram and TikTok as social media "
            "manager, doubling engagement through content creation and Canva graphics. "
            "Led an awareness campaign for local nonprofit fundraising."
        ),
    },
    {
        "name": "Daniel Okafor",
        "headline": "Carnegie Mellon Class of 2030 | Aspiring CS major",
        "location": "Lagos, NG",
        "profile_url": "https://example.com/in/daniel-okafor",
        "email": "daniel.sample@example.com",
        "grad_year": 2030,
        "school": "Carnegie Mellon University",
        "source": "sample",
        "raw_text": (
            "Competitive programmer and robotics team captain. Built a personal "
            "website and won two hackathons. Interested in machine learning."
        ),
    },
    {
        "name": "Priya Sharma",
        "headline": "CMU '30 | Marketing & PR | Volunteer",
        "location": "San Jose, CA",
        "profile_url": "https://example.com/in/priya-sharma",
        "email": "priya.sample@example.com",
        "grad_year": 2030,
        "school": "Carnegie Mellon University",
        "source": "sample",
        "raw_text": (
            "Volunteer at the Red Cross and habitat for humanity. Member of the "
            "school PR team handling advertising, newsletters, and copywriting. "
            "Did graphic design and branding for the yearbook."
        ),
    },
    {
        "name": "Liam Chen",
        "headline": "Stanford Class of 2030 | Debate",
        "location": "Palo Alto, CA",
        "profile_url": "https://example.com/in/liam-chen",
        "email": None,
        "grad_year": 2030,
        "school": "Stanford University",   # not CMU — should be filtered out
        "source": "sample",
        "raw_text": "Varsity debate, community outreach and fundraising chair.",
    },
    {
        "name": "Sofia Martinez",
        "headline": "Carnegie Mellon University | Class of 2029",
        "location": "Miami, FL",
        "profile_url": "https://example.com/in/sofia-martinez",
        "email": None,
        "grad_year": 2029,                 # wrong year — should be filtered out
        "school": "Carnegie Mellon University",
        "source": "sample",
        "raw_text": "Social media manager and marketing intern, event planning lead.",
    },
]
