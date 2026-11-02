class Abstractable:
    """Root of the output mixins; lets a subclass be declared abstract.

    `class Entity(Record, Identifier, abstract=True)` marks a base class that
    is never stored or instantiated. The mixins skip their setup for it, so
    fields like `id_field` and `time_field` are declared on the first concrete
    subclass, after it has had the chance to rename them.

    Every mixin's `__init_subclass__` takes `abstract` and passes it on; this
    class consumes it so it never reaches `object`.
    """

    def __init_subclass__(cls, abstract: bool = False, **kwargs):
        super().__init_subclass__(**kwargs)
