from .models import Goal, Message, Resource, Workspace


def prepared_workshop(revision: int = 1) -> Workspace:
    return Workspace(
        revision=revision,
        goal=Goal(),
        resources=[
            Resource(
                id="room",
                name="Room hold",
                kind="room_hold",
                status="active",
                content="Room 42 is held for 20 attendees on 30 October 2026 at 14:00 Asia/Kolkata.",
                depends_on=["room_required", "venue", "date", "time", "attendees"],
                assumption="A physical room in Room 42 on 2026-10-30 at 14:00 is needed for 20 attendees.",
            ),
            Resource(
                id="invitation",
                name="Invitation draft",
                kind="draft",
                status="draft",
                content="Join our Practical Python workshop in Room 42 on 30 October 2026 at 14:00 Asia/Kolkata. Please attend in person.",
                depends_on=["delivery", "venue", "date", "time", "meeting_url", "topic"],
                assumption="All attendees should travel to Room 42 on 2026-10-30 at 14:00 for Practical Python.",
            ),
            Resource(
                id="handouts",
                name="Handout instructions",
                kind="draft",
                status="draft",
                content="Print 20 copies of the Practical Python exercises. Hand one to each attendee in the room.",
                depends_on=["delivery", "room_required", "topic", "attendees"],
                assumption="Every attendee receives printed Practical Python exercises in person.",
            ),
            Resource(
                id="slides",
                name="Slide outline",
                kind="draft",
                status="draft",
                content="Practical Python\n1. Readable functions\n2. Working with data\n3. A small automation project\n4. Questions and practice",
                depends_on=["topic"],
                assumption="The workshop covers Practical Python.",
            ),
            Resource(
                id="catering",
                name="Catering request",
                kind="sent_request",
                status="sent",
                content="Please provide refreshments for 20 attendees in Room 42 on 30 October 2026 at 14:00 Asia/Kolkata.",
                depends_on=["catering_required", "venue", "date", "time", "attendees"],
                assumption="Refreshments for 20 attendees in Room 42 on 2026-10-30 at 14:00 are still needed.",
            ),
            Resource(
                id="sponsor",
                name="Sponsor note",
                kind="draft",
                status="draft",
                content="Thank you for supporting next month's community meetup. This note is unrelated to the Python workshop.",
                depends_on=[],
                assumption="This independent sponsor note is for another event.",
            ),
        ],
        messages=[
            Message(
                id="original-catering",
                subject="Workshop refreshments",
                content="Please provide refreshments for 20 attendees in Room 42 on 30 October 2026 at 14:00 Asia/Kolkata.",
                created_at="2026-10-23T09:00:00+00:00",
            )
        ],
    )
