from typing import TYPE_CHECKING, Union

from django.contrib.postgres.indexes import GinIndex
from django.db import models
from django.db.models import F, Q

from ...account.utils import requestor_is_staff_member_or_app
from ...core.db.fields import SanitizedJSONField
from ...core.models import ModelWithMetadata, SortableModel
from ...core.units import MeasurementUnits
from ...core.utils.editorjs import clean_editor_js
from ...core.utils.translations import TranslationProxy
from ...page.models import PageType
from ...product.models import ProductType
from .. import AttributeEntityType, AttributeInputType, AttributeType

if TYPE_CHECKING:
    from django.db.models import OrderBy

    from ...account.models import User
    from ...app.models import App


class BaseAssignedAttribute(models.Model):
    assignment = None

    class Meta:
        abstract = True

    @property
    def attribute(self):
        return self.assignment.attribute

    @property
    def attribute_pk(self):
        return self.assignment.attribute_id


class BaseAttributeQuerySet(models.QuerySet):
    def get_public_attributes(self):
        raise NotImplementedError

    def get_visible_to_user(self, requestor: Union["User", "App"]):
        if requestor_is_staff_member_or_app(requestor):
            return self.all()
        return self.get_public_attributes()


class AssociatedAttributeQuerySet(BaseAttributeQuerySet):
    def get_public_attributes(self):
        return self.filter(attribute__visible_in_storefront=True)


class AttributeQuerySet(BaseAttributeQuerySet):
    def get_unassigned_product_type_attributes(self, product_type_pk: int):
        return self.product_type_attributes().exclude(
            Q(attributeproduct__product_type_id=product_type_pk)
            | Q(attributevariant__product_type_id=product_type_pk)
        )

    def get_unassigned_page_type_attributes(self, page_type_pk: int):
        return self.page_type_attributes().exclude(
            attributepage__page_type_id=page_type_pk
        )

    def get_assigned_product_type_attributes(self, product_type_pk: int):
        return self.product_type_attributes().filter(
            Q(attributeproduct__product_type_id=product_type_pk)
            | Q(attributevariant__product_type_id=product_type_pk)
        )

    def get_assigned_page_type_attributes(self, product_type_pk: int):
        return self.page_type_attributes().filter(
            Q(attributepage__page_type_id=product_type_pk)
        )

    def get_public_attributes(self):
        return self.filter(visible_in_storefront=True)

    def _get_sorted_m2m_field(self, m2m_field_name: str, asc: bool):
        sort_order_field = F(f"{m2m_field_name}__sort_order")
        id_field = F(f"{m2m_field_name}__id")
        if asc:
            sort_method = sort_order_field.asc(nulls_last=True)
            id_sort: Union["OrderBy", "F"] = id_field
        else:
            sort_method = sort_order_field.desc(nulls_first=True)
            id_sort = id_field.desc()

        return self.order_by(sort_method, id_sort)

    def product_attributes_sorted(self, asc=True):
        return self._get_sorted_m2m_field("attributeproduct", asc)

    def variant_attributes_sorted(self, asc=True):
        return self._get_sorted_m2m_field("attributevariant", asc)

    def product_type_attributes(self):
        return self.filter(type=AttributeType.PRODUCT_TYPE)

    def page_type_attributes(self):
        return self.filter(type=AttributeType.PAGE_TYPE)


class Attribute(ModelWithMetadata):
    slug = models.SlugField(max_length=250, unique=True, allow_unicode=True)
    name = models.CharField(max_length=255)
    type = models.CharField(max_length=50, choices=AttributeType.CHOICES)

    input_type = models.CharField(
        max_length=50,
        choices=AttributeInputType.CHOICES,
        default=AttributeInputType.DROPDOWN,
    )
    entity_type = models.CharField(
        max_length=50, choices=AttributeEntityType.CHOICES, blank=True, null=True
    )

    product_types = models.ManyToManyField(
        ProductType,
        blank=True,
        related_name="product_attributes",
        through="AttributeProduct",
        through_fields=("attribute", "product_type"),
    )
    product_variant_types = models.ManyToManyField(
        ProductType,
        blank=True,
        related_name="variant_attributes",
        through="AttributeVariant",
        through_fields=("attribute", "product_type"),
    )
    page_types = models.ManyToManyField(
        PageType,
        blank=True,
        related_name="page_attributes",
        through="AttributePage",
        through_fields=("attribute", "page_type"),
    )

    unit = models.CharField(
        max_length=100,
        choices=MeasurementUnits.CHOICES,  # type: ignore
        blank=True,
        null=True,
    )
    value_required = models.BooleanField(default=False, blank=True)
    is_variant_only = models.BooleanField(default=False, blank=True)
    visible_in_storefront = models.BooleanField(default=True, blank=True)

    filterable_in_storefront = models.BooleanField(default=False, blank=True)
    filterable_in_dashboard = models.BooleanField(default=False, blank=True)

    storefront_search_position = models.IntegerField(default=0, blank=True)
    available_in_grid = models.BooleanField(default=False, blank=True)

    objects = models.Manager.from_queryset(AttributeQuerySet)()
    translated = TranslationProxy()

    class Meta(ModelWithMetadata.Meta):
        ordering = ("storefront_search_position", "slug")

    def __str__(self) -> str:
        return self.name

    def has_values(self) -> bool:
        return self.values.exists()


class AttributeTranslation(models.Model):
    language_code = models.CharField(max_length=10)
    attribute = models.ForeignKey(
        Attribute, related_name="translations", on_delete=models.CASCADE
    )
    name = models.CharField(max_length=100)

    class Meta:
        unique_together = (("language_code", "attribute"),)

    def __repr__(self):
        class_ = type(self)
        return "%s(pk=%r, name=%r, attribute_pk=%r)" % (
            class_.__name__,
            self.pk,
            self.name,
            self.attribute_id,
        )

    def __str__(self) -> str:
        return self.name


class AttributeValue(SortableModel):
    name = models.CharField(max_length=250)
    value = models.CharField(max_length=100, blank=True, default="")
    slug = models.SlugField(max_length=255, allow_unicode=True)
    file_url = models.URLField(null=True, blank=True)
    content_type = models.CharField(max_length=50, null=True, blank=True)
    attribute = models.ForeignKey(
        Attribute, related_name="values", on_delete=models.CASCADE
    )
    rich_text = SanitizedJSONField(blank=True, null=True, sanitizer=clean_editor_js)
    boolean = models.BooleanField(blank=True, null=True)
    date_time = models.DateTimeField(blank=True, null=True)

    translated = TranslationProxy()

    class Meta:
        ordering = ("sort_order", "pk")
        unique_together = ("slug", "attribute")
        indexes = [GinIndex(fields=["name", "slug"])]

    def __str__(self) -> str:
        return self.name

    @property
    def input_type(self):
        return self.attribute.input_type

    def get_ordering_queryset(self):
        return self.attribute.values.all()


class AttributeValueTranslation(models.Model):
    language_code = models.CharField(max_length=10)
    attribute_value = models.ForeignKey(
        AttributeValue, related_name="translations", on_delete=models.CASCADE
    )
    name = models.CharField(max_length=100)
    rich_text = SanitizedJSONField(blank=True, null=True, sanitizer=clean_editor_js)

    class Meta:
        unique_together = (("language_code", "attribute_value"),)

    def __repr__(self) -> str:
        class_ = type(self)
        return "%s(pk=%r, name=%r, attribute_value_pk=%r)" % (
            class_.__name__,
            self.pk,
            self.name,
            self.attribute_value_id,
        )

    def __str__(self) -> str:
        return self.name
