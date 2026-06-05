"""
Country flag emojis keyed by team name (lowercase).
Covers all FIFA World Cup 2026 qualified/expected nations plus common variants.
team_label() is the main entry point — returns "🇧🇷 Brazil" or just "Brazil" if unknown.
"""

TEAM_FLAGS: dict[str, str] = {
    # UEFA
    "germany":                  "🇩🇪",
    "france":                   "🇫🇷",
    "spain":                    "🇪🇸",
    "england":                  "🏴󠁧󠁢󠁥󠁮󠁧󠁿",
    "portugal":                 "🇵🇹",
    "netherlands":              "🇳🇱",
    "belgium":                  "🇧🇪",
    "croatia":                  "🇭🇷",
    "switzerland":              "🇨🇭",
    "serbia":                   "🇷🇸",
    "austria":                  "🇦🇹",
    "hungary":                  "🇭🇺",
    "turkey":                   "🇹🇷",
    "türkiye":                  "🇹🇷",
    "slovakia":                 "🇸🇰",
    "scotland":                 "🏴󠁧󠁢󠁳󠁣󠁴󠁿",
    "wales":                    "🏴󠁧󠁢󠁷󠁬󠁳󠁿",
    "romania":                  "🇷🇴",
    "denmark":                  "🇩🇰",
    "ukraine":                  "🇺🇦",
    "czech republic":           "🇨🇿",
    "czechia":                  "🇨🇿",
    "albania":                  "🇦🇱",
    "poland":                   "🇵🇱",
    "sweden":                   "🇸🇪",
    "norway":                   "🇳🇴",
    "italy":                    "🇮🇹",
    "greece":                   "🇬🇷",
    "iceland":                  "🇮🇸",
    "finland":                  "🇫🇮",
    "israel":                   "🇮🇱",
    "georgia":                  "🇬🇪",
    "slovenia":                 "🇸🇮",
    "north macedonia":          "🇲🇰",
    "bosnia-herzegovina":       "🇧🇦",
    "bosnia and herzegovina":   "🇧🇦",
    "northern ireland":         "🇬🇧",
    "luxembourg":               "🇱🇺",
    "armenia":                  "🇦🇲",

    # CONMEBOL
    "argentina":                "🇦🇷",
    "brazil":                   "🇧🇷",
    "colombia":                 "🇨🇴",
    "ecuador":                  "🇪🇨",
    "uruguay":                  "🇺🇾",
    "venezuela":                "🇻🇪",
    "bolivia":                  "🇧🇴",
    "paraguay":                 "🇵🇾",
    "chile":                    "🇨🇱",
    "peru":                     "🇵🇪",

    # CONCACAF
    "united states":            "🇺🇸",
    "usa":                      "🇺🇸",
    "mexico":                   "🇲🇽",
    "canada":                   "🇨🇦",
    "honduras":                 "🇭🇳",
    "jamaica":                  "🇯🇲",
    "panama":                   "🇵🇦",
    "costa rica":               "🇨🇷",
    "trinidad and tobago":      "🇹🇹",
    "trinidad & tobago":        "🇹🇹",
    "guatemala":                "🇬🇹",
    "el salvador":              "🇸🇻",
    "cuba":                     "🇨🇺",
    "haiti":                    "🇭🇹",
    "curacao":                  "🇨🇼",
    "nicaragua":                "🇳🇮",
    "belize":                   "🇧🇿",

    # CAF
    "morocco":                  "🇲🇦",
    "senegal":                  "🇸🇳",
    "nigeria":                  "🇳🇬",
    "egypt":                    "🇪🇬",
    "south africa":             "🇿🇦",
    "ivory coast":              "🇨🇮",
    "côte d'ivoire":            "🇨🇮",
    "cote d'ivoire":            "🇨🇮",
    "ghana":                    "🇬🇭",
    "cameroon":                 "🇨🇲",
    "tunisia":                  "🇹🇳",
    "dr congo":                 "🇨🇩",
    "congo dr":                 "🇨🇩",
    "democratic republic of congo": "🇨🇩",
    "algeria":                  "🇩🇿",
    "mali":                     "🇲🇱",
    "zambia":                   "🇿🇲",
    "guinea":                   "🇬🇳",
    "cape verde":               "🇨🇻",
    "gabon":                    "🇬🇦",
    "ethiopia":                 "🇪🇹",
    "rwanda":                   "🇷🇼",
    "mozambique":               "🇲🇿",
    "tanzania":                 "🇹🇿",
    "angola":                   "🇦🇴",
    "benin":                    "🇧🇯",
    "burkina faso":             "🇧🇫",

    # AFC
    "japan":                    "🇯🇵",
    "south korea":              "🇰🇷",
    "korea republic":           "🇰🇷",
    "korea, republic of":       "🇰🇷",
    "iran":                     "🇮🇷",
    "australia":                "🇦🇺",
    "saudi arabia":             "🇸🇦",
    "qatar":                    "🇶🇦",
    "iraq":                     "🇮🇶",
    "uzbekistan":               "🇺🇿",
    "jordan":                   "🇯🇴",
    "bahrain":                  "🇧🇭",
    "china pr":                 "🇨🇳",
    "china":                    "🇨🇳",
    "indonesia":                "🇮🇩",
    "oman":                     "🇴🇲",
    "uae":                      "🇦🇪",
    "united arab emirates":     "🇦🇪",
    "thailand":                 "🇹🇭",
    "vietnam":                  "🇻🇳",
    "kyrgyzstan":               "🇰🇬",
    "tajikistan":               "🇹🇯",
    "india":                    "🇮🇳",
    "palestine":                "🇵🇸",
    "myanmar":                  "🇲🇲",
    "philippines":              "🇵🇭",
    "singapore":                "🇸🇬",
    "malaysia":                 "🇲🇾",

    # OFC
    "new zealand":              "🇳🇿",
    "fiji":                     "🇫🇯",
    "solomon islands":          "🇸🇧",
    "papua new guinea":         "🇵🇬",
    "tahiti":                   "🇵🇫",
    "vanuatu":                  "🇻🇺",
}


def flag_for(team_name: str) -> str:
    """Return the flag emoji for a team, or empty string if not in the map."""
    return TEAM_FLAGS.get(team_name.lower().strip(), "")


def team_label(team_name: str) -> str:
    """
    Return 'FLAG Name' for display on buttons and messages.
    Falls back to plain name if no flag is mapped.
    e.g. team_label("Brazil") → "🇧🇷 Brazil"
         team_label("Test United") → "Test United"
    """
    flag = flag_for(team_name)
    return f"{flag} {team_name}" if flag else team_name
