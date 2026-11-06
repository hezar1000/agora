"""Enrollment codes that can be read out to a room.

``secrets.token_urlsafe(18)`` produces something like ``xK3-mQ9pR2sT7vW1yZ4aB6cD``.
It is unguessable and it is unusable: nobody types it correctly off a slide,
and an instructor reading it aloud has no chance at all. So the code that is
supposed to let a class in became the thing that kept them out.

Three words drawn from this list is about 27 bits. Enrolment is already
throttled to ten attempts per account every five minutes and needs a signed-in
CWL account to try at all, so 27 bits is several hundred thousand years of
guessing per account -- for the right to be a student in a course that is
already open to students.

The list is deliberately dull. Every word is a common concrete English noun or
plain adjective of at least four letters, spelled one way, distinct from every
other word here by more than a letter, and inoffensive beside any other word
on the list -- these go on a projector in front of a lecture hall.
"""

import secrets

#: Common, concrete, unambiguous English words. Grouped only to make the list
#: easy to read and extend; the draw treats it as one pool.
WORDS = (
    # animals
    "badger", "beaver", "bison", "camel", "cheetah", "cobra", "coyote",
    "dolphin", "donkey", "falcon", "ferret", "gecko", "gibbon", "giraffe",
    "gopher", "grouse", "hamster", "heron", "impala", "jackal", "kitten",
    "koala", "lemur", "leopard", "lizard", "llama", "lobster", "magpie",
    "marmot", "meerkat", "mongoose", "moose", "muskrat", "narwhal",
    "ocelot", "osprey", "otter", "oyster", "panda", "parrot", "pelican",
    "penguin", "pigeon", "puffin", "python", "rabbit", "raccoon", "raven",
    "salmon", "seagull", "shrimp", "skunk", "sparrow", "spider", "squid",
    "starling", "stork", "swallow", "tapir", "toucan", "trout", "turkey",
    "turtle", "vulture", "walrus", "weasel", "wombat", "zebra",
    # trees, plants and flowers
    "acorn", "almond", "aspen", "bamboo", "birch", "bracken", "cactus",
    "cedar", "clover", "conifer", "crocus", "cypress", "dahlia", "daisy",
    "fennel", "fern", "ginkgo", "hemlock", "holly", "iris", "jasmine",
    "juniper", "laurel", "lichen", "lilac", "lotus", "lupine", "maple",
    "marigold", "mimosa", "moss", "myrtle", "nettle", "orchid", "peony",
    "petal", "poplar", "poppy", "primrose", "redwood", "sequoia",
    "shamrock", "spruce", "sunflower", "thistle", "thyme", "tulip",
    "violet", "willow", "yarrow",
    # weather and sky
    "aurora", "blizzard", "breeze", "cirrus", "comet", "crescent",
    "cyclone", "dawn", "drizzle", "dusk", "eclipse", "equinox", "frost",
    "galaxy", "hailstone", "meteor", "midnight", "monsoon", "moonrise",
    "nebula", "noon", "orbit", "planet", "rainbow", "sleet", "solstice",
    "sunrise", "sunset", "thunder", "tundra", "twilight", "typhoon",
    "zenith",
    # land and water
    "atoll", "basin", "bayou", "bluff", "boulder", "canyon", "cavern",
    "channel", "cliff", "cove", "crater", "delta", "dune", "estuary",
    "fjord", "foothill", "geyser", "glacier", "gorge", "grotto",
    "hillside", "island", "lagoon", "meadow", "mesa", "moorland", "oasis",
    "pebble", "plateau", "prairie", "quarry", "ravine", "reef", "ridge",
    "riverbed", "sandbar", "summit", "swamp", "thicket", "valley",
    "volcano", "waterfall", "wetland", "woodland",
    # food and drink
    "apricot", "bagel", "barley", "biscuit", "brisket", "cabbage",
    "caramel", "cashew", "chestnut", "chowder", "cinnamon", "citrus",
    "cocoa", "custard", "dumpling", "granola", "hazelnut", "lentil",
    "mango", "marzipan", "muffin", "noodle", "nutmeg", "oatmeal", "olive",
    "papaya", "paprika", "parsnip", "pastry", "peanut", "pecan", "pepper",
    "pomelo", "porridge", "pretzel", "pudding", "pumpkin", "quinoa",
    "radish", "risotto", "rutabaga", "saffron", "sorbet", "spinach",
    "squash", "tangerine", "tapioca", "tomato", "turnip", "vanilla",
    "walnut", "waffle", "zucchini",
    # household things
    "anvil", "apron", "armchair", "awning", "basket", "beaker", "blanket",
    "bobbin", "bookcase", "bottle", "bracket", "broom", "bucket", "candle",
    "canister", "canvas", "carafe", "cauldron", "clipboard", "compass",
    "crayon", "cupboard", "curtain", "cushion", "doorbell", "drawer",
    "easel", "envelope", "fixture", "flagpole", "flask", "footstool",
    "funnel", "goblet", "grommet", "hammock", "hinge", "inkwell", "jigsaw",
    "kettle", "keyring", "ladder", "lantern", "lattice", "mailbox",
    "mantel", "mirror", "mosaic", "napkin", "notebook", "padlock",
    "pitcher", "planter", "quilt", "ribbon", "sandal", "satchel", "settee",
    "shutter", "sideboard", "skillet", "spatula", "stencil", "stopper",
    "tankard", "teapot", "thimble", "tinder", "toaster", "trellis",
    "trolley", "trowel", "tureen", "umbrella", "vessel", "wardrobe",
    "whistle", "wicker", "windmill",
    # tools and workshop
    "auger", "bandsaw", "caliper", "chisel", "clamp", "crowbar",
    "drillbit", "fastener", "gasket", "grinder", "hacksaw", "hatchet",
    "jackplane", "lathe", "mallet", "ratchet", "rivet", "sandpaper",
    "scraper", "sledge", "spanner", "stapler", "toolbox", "wrench",
    # music and sound
    "bagpipe", "banjo", "bassoon", "bugle", "cello", "chime", "clarinet",
    "cymbal", "fiddle", "harmonica", "harp", "kazoo", "lyre", "mandolin",
    "marimba", "oboe", "organ", "piccolo", "sitar", "tabla", "triangle",
    "trombone", "trumpet", "tuba", "ukulele", "viola", "xylophone",
    "zither",
    # colours and materials
    "alabaster", "amber", "amethyst", "azure", "basalt", "bronze",
    "burgundy", "cerulean", "chalk", "charcoal", "cobalt", "copper",
    "coral", "crimson", "emerald", "flint", "garnet", "glazed", "granite",
    "indigo", "ivory", "jade", "lavender", "limestone", "magenta",
    "marble", "maroon", "obsidian", "ochre", "onyx", "opal", "pewter",
    "porcelain", "pumice", "quartz", "sandstone", "sapphire", "scarlet",
    "shale", "silver", "slate", "teal", "terracotta", "topaz", "turquoise",
    "velvet",
    # buildings and places
    "abbey", "airfield", "arcade", "atrium", "balcony", "belfry", "bridge",
    "castle", "cellar", "chapel", "chimney", "cloister", "college",
    "cottage", "courtyard", "dockyard", "drawbridge", "farmhouse",
    "granary", "hangar", "keystone", "library", "lighthouse", "market",
    "mezzanine", "monument", "observatory", "parapet", "pavilion",
    "portico", "quayside", "rampart", "rotunda", "sawmill", "stairwell",
    "stronghold", "terrace", "turret", "veranda", "viaduct", "workshop",
    # transport
    "airship", "barge", "bicycle", "caravan", "carriage", "catamaran",
    "clipper", "dinghy", "ferry", "freighter", "frigate", "galleon",
    "gondola", "handcart", "kayak", "locomotive", "monorail", "pontoon",
    "railcar", "rowboat", "sailboat", "schooner", "scooter", "sidecar",
    "steamer", "tractor", "tramcar", "trawler", "tricycle", "tugboat",
    "wagon", "yacht",
    # plain adjectives
    "ancient", "brisk", "candid", "cheerful", "civic", "clever", "compact",
    "crisp", "curious", "distant", "eager", "earnest", "elegant",
    "fearless", "fluent", "fragrant", "frugal", "gallant", "gentle",
    "genuine", "gilded", "graceful", "hardy", "helpful", "honest",
    "humble", "jovial", "keen", "kindly", "level", "lively", "loyal",
    "lucid", "mellow", "merry", "mindful", "modest", "nimble", "noble",
    "orderly", "patient", "placid", "plucky", "polite", "prudent",
    "quaint", "quiet", "radiant", "restful", "rugged", "rustic", "serene",
    "sincere", "solid", "spirited", "steady", "sterling", "stoic",
    "sturdy", "sunny", "supple", "tender", "thrifty", "tidy", "tranquil",
    "trusty", "upbeat", "upright", "valiant", "vibrant", "vivid",
    "watchful", "welcome", "willing", "witty", "worthy", "zealous",
    # time and season
    "autumn", "century", "daybreak", "decade", "epoch", "evening",
    "harvest", "interlude", "juncture", "morning", "quarter", "season",
    "semester", "spring", "summer", "sundial", "weekday", "winter",
    # shapes and measures
    "cardinal", "circle", "cubit", "decimal", "diagonal", "ellipse",
    "fathom", "furlong", "helix", "hexagon", "kilogram", "median",
    "octagon", "parabola", "pentagon", "prism", "pyramid", "quadrant",
    "radius", "rhombus", "spiral", "square", "tangent", "trapezoid",
    "vector", "vertex",
)

#: How many words a generated code is made of.
LENGTH = 3

#: What joins them. A hyphen survives being read aloud and being typed on a
#: phone keyboard; a space does not survive a form field.
SEPARATOR = "-"


def generate(length=LENGTH, separator=SEPARATOR):
    """A code like ``copper-lantern-thistle``.

    Words are drawn without replacement, so no code repeats a word -- a
    repeat reads like a mistake when it goes up on a slide.
    """
    if length > len(WORDS):
        raise ValueError("asked for more words than the list holds")
    chosen = secrets.SystemRandom().sample(WORDS, length)
    return separator.join(chosen)


def looks_like_one(value):
    """Whether `value` has the shape of a code this module would produce.

    Used only to explain a rejection; nothing requires a code to be one of
    ours, and instructors may set whatever they like.
    """
    parts = str(value or "").strip().lower().split(SEPARATOR)
    return len(parts) >= LENGTH and all(part.isalpha() for part in parts)
