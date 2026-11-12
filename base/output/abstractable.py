class Abstractable:
    """Root of the output mixins; lets a subclass be declared abstract.

    `class Entity(Record, Identifier, abstract=True)` marks a base class that
    is never stored or instantiated. The mixins skip their setup for it, so
    fields like `id_field` and `time_field` are declared on the first concrete
    subclass, after it has had the chance to rename them.

    Every mixin's `__init_subclass__` takes `abstract` and passes it on; this
    class consumes it so it never reaches `object`.

    It also ends the mixins' `__post_init__` chain, so each mixin can call
    `super().__post_init__()` whatever it is combined with.
    """

    def __init_subclass__(cls, abstract: bool = False, **kwargs):
        super().__init_subclass__(**kwargs)

    def __post_init__(self):
        # Passed on in case a class from outside the mixins follows.
        parent_post_init = getattr(super(), "__post_init__", None)
        if parent_post_init is not None:
            parent_post_init()
