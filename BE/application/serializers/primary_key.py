from rest_framework import serializers


class ReadOnlyPrimaryKeyMixin:
    """The model's primary key is never read from the request body.

    On update the row the view loaded is saved in place and the answer shows its
    key; on create the model default generates the key. Only the top-level
    serializer (or the child of a top-level ``many=True`` list) is affected: a
    serializer nested as a field of another one validates the key it is given.
    """

    def get_fields(self):
        fields = super().get_fields()
        parent = self.parent
        if isinstance(parent, serializers.ListSerializer):
            parent = parent.parent
        if parent is None:
            field = fields.get(self.Meta.model._meta.pk.name)
            if field is not None:
                field.read_only = True
                field.required = False
        return fields
