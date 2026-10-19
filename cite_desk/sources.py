"""Every document in the corpus: where it comes from, under what terms, and which parser reads it.

Only official sources. `python -m cite_desk fetch` downloads them, `python -m cite_desk build` parses them into
corpus/<collection>/<doc>.jsonl (committed, so the app never needs the network to know what the law says).
"""
from __future__ import annotations

from dataclasses import dataclass, field

US_GOV = "Public domain: a work of the US federal government (17 U.S.C. § 105)."
EUR_LEX = ("© European Union, https://eur-lex.europa.eu. Reuse authorised under Commission Decision 2011/833/EU. "
           "Only the Official Journal edition is authentic.")
UK_OGL = "Contains public sector information licensed under the Open Government Licence v3.0."
CANADA = ("Reproduced under the Reproduction of Federal Law Order (SI/97-5). This is not the official version; "
          "the official version is published on the Justice Laws Website.")


@dataclass(frozen=True)
class Doc:
    id: str
    title: str
    short: str  # how citations name it: "GDPR", "PECR", "15 U.S.C."
    jurisdiction: str  # US | EU | UK | CA | NG | NG-LA
    kind: str  # statute | regulation | guidance
    fetch_url: str
    home_url: str
    parser: str
    licence: str
    ext: str = "html"
    headers: dict = field(default_factory=dict)
    note: str = ""


@dataclass(frozen=True)
class Collection:
    id: str
    title: str
    blurb: str
    docs: tuple[Doc, ...]
    examples: tuple[str, ...]


OUTREACH = Collection(
    id="outreach",
    title="Outreach compliance",
    blurb="The rules on cold email, calls, texts and personal data in the US, EU, UK, Canada and Nigeria.",
    examples=(
        "Do I need to include a physical address in a cold sales email to US prospects?",
        "Can I email a sales pitch to a sole trader in the UK without their consent?",
        "How quickly do I have to honour an unsubscribe request under CAN-SPAM?",
        "Can legitimate interest be a lawful basis for direct marketing under the GDPR?",
        "What hours am I allowed to make telemarketing calls to US residences?",
        "What is the maximum penalty for sending spam in Canada?",
    ),
    docs=(
        Doc("us-canspam-act", "CAN-SPAM Act (15 U.S.C. ch. 103)", "15 U.S.C.", "US", "statute",
            "https://www.govinfo.gov/content/pkg/USCODE-2024-title15/html/USCODE-2024-title15-chap103.htm",
            "https://uscode.house.gov/view.xhtml?path=/prelim@title15/chapter103&edition=prelim",
            "uscode", US_GOV, note="US Code, 2024 edition, via govinfo.gov."),
        Doc("us-canspam-rule", "FTC CAN-SPAM Rule (16 CFR Part 316)", "16 CFR", "US", "regulation",
            "https://www.ecfr.gov/api/versioner/v1/full/{ecfr_date}/title-16.xml?part=316",
            "https://www.ecfr.gov/current/title-16/chapter-I/subchapter-C/part-316",
            "ecfr", US_GOV, ext="xml", note="Electronic Code of Federal Regulations, current."),
        Doc("us-ftc-canspam-guide", "CAN-SPAM Act: A Compliance Guide for Business (FTC)", "FTC guide", "US", "guidance",
            "https://www.ftc.gov/business-guidance/resources/can-spam-act-compliance-guide-business",
            "https://www.ftc.gov/business-guidance/resources/can-spam-act-compliance-guide-business",
            "ftc_guide", US_GOV, headers={"User-Agent": "Mozilla/5.0 (Macintosh) research-bot"},
            note="Plain-English guidance, not the law itself."),
        Doc("us-tcpa", "Telephone Consumer Protection Act (47 U.S.C. § 227)", "47 U.S.C.", "US", "statute",
            "https://www.govinfo.gov/content/pkg/USCODE-2024-title47/html/USCODE-2024-title47-chap5-subchapII-partI-sec227.htm",
            "https://uscode.house.gov/view.xhtml?req=granuleid:USC-prelim-title47-section227&num=0&edition=prelim",
            "uscode", US_GOV, note="US Code, 2024 edition, via govinfo.gov."),
        Doc("us-tcpa-rules", "FCC telemarketing and robocall rules (47 CFR § 64.1200)", "47 CFR", "US", "regulation",
            "https://www.ecfr.gov/api/versioner/v1/full/{ecfr_date}/title-47.xml?part=64&section=64.1200",
            "https://www.ecfr.gov/current/title-47/chapter-I/subchapter-B/part-64/subpart-L/section-64.1200",
            "ecfr", US_GOV, ext="xml", note="Electronic Code of Federal Regulations, current."),
        Doc("eu-gdpr", "General Data Protection Regulation (EU) 2016/679", "GDPR", "EU", "regulation",
            "https://publications.europa.eu/resource/celex/32016R0679",
            "https://eur-lex.europa.eu/eli/reg/2016/679/oj",
            "eurlex_oj", EUR_LEX, headers={"Accept": "application/xhtml+xml", "Accept-Language": "eng"},
            note="Official Journal text, articles and recitals."),
        Doc("eu-eprivacy", "ePrivacy Directive 2002/58/EC (consolidated)", "ePrivacy Directive", "EU", "statute",
            "https://eur-lex.europa.eu/legal-content/EN/TXT/HTML/?uri=CELEX:02002L0058-20091219",
            "https://eur-lex.europa.eu/legal-content/EN/TXT/?uri=CELEX:02002L0058-20091219",
            "eurlex_consolidated", EUR_LEX, headers={"User-Agent": "Mozilla/5.0 (Macintosh) research-bot"},
            note="Consolidated text as of 19 December 2009. Member States implement it in national law."),
        Doc("uk-pecr", "Privacy and Electronic Communications Regulations 2003 (SI 2003/2426)", "PECR", "UK", "regulation",
            "https://www.legislation.gov.uk/uksi/2003/2426/data.xml",
            "https://www.legislation.gov.uk/uksi/2003/2426/contents",
            "legislation_uk", UK_OGL, ext="xml", note="Latest revised version, including Data (Use and Access) Act 2025 changes in force."),
        Doc("ca-casl", "Canada's Anti-Spam Legislation (S.C. 2010, c. 23)", "CASL", "CA", "statute",
            "https://laws-lois.justice.gc.ca/eng/XML/E-1.6.xml",
            "https://laws-lois.justice.gc.ca/eng/acts/E-1.6/",
            "justice_ca", CANADA, ext="xml"),
        Doc("ng-ndpc-gaid", "NDP Act General Application and Implementation Directive (GAID) 2025", "GAID 2025", "NG", "regulation",
            "https://ndpc.gov.ng/wp-content/uploads/2025/07/NDP-ACT-GAID-2025-MARCH-20TH.pdf",
            "https://ndpc.gov.ng/",
            "ndpc_gaid", "Official publication of the Nigeria Data Protection Commission, reproduced for reference with attribution.",
            ext="pdf", note="Issued by the NDPC under the Nigeria Data Protection Act 2023. The Act itself is not in this collection."),
    ),
)

LAGOS = Collection(
    id="lagos-tenancy",
    title="Lagos tenancy",
    blurb="The Tenancy Law of Lagos State 2011: rent in advance, notices to quit, recovery of premises.",
    examples=(
        "How much rent in advance can a landlord collect from a new yearly tenant in Lagos?",
        "Does the Lagos Tenancy Law apply to a flat in Ikoyi?",
        "How much notice must a landlord give a monthly tenant?",
        "Can a landlord lock a tenant out without going to court?",
    ),
    docs=(
        Doc("lagos-tenancy-law", "Tenancy Law of Lagos State 2011 (Law No. 14 of 2011)", "Tenancy Law 2011", "NG-LA", "statute",
            "https://lagosministryofjustice.org/wp-content/uploads/2022/01/Tenancy-Law-2011.pdf",
            "https://lagosministryofjustice.org/",
            "lagos_law", "Lagos State Official Gazette No. 37, Vol. 44 (26 August 2011), as published by the Lagos State "
            "Ministry of Justice. Reproduced for reference.", ext="pdf",
            note="Listed as under review by the Lagos State Law Reform Commission."),
    ),
)

COLLECTIONS: dict[str, Collection] = {c.id: c for c in (OUTREACH, LAGOS)}
DOCS: dict[str, Doc] = {d.id: d for c in COLLECTIONS.values() for d in c.docs}


def collection_of(doc_id: str) -> Collection:
    return next(c for c in COLLECTIONS.values() if any(d.id == doc_id for d in c.docs))
