from django.template.loader import render_to_string
from django.http import HttpResponse
from django import forms
from django.urls import reverse
from django.utils.html import escape


class SelectWithPop(forms.Select):
    def __init__(self, href, *args, **kwargs):
        self.href = href
        self.name = None
        if "name" in kwargs:
            self.name = kwargs.pop("name")

        super(SelectWithPop, self).__init__(*args, **kwargs)

    def render(self, name, *args, **kwargs):
        return render_to_string(
            "select-with-popup.html",
            {
                "field": self.name or name,
                "href": self.href,
                "select_field": super(SelectWithPop, self).render(
                    name, *args, **kwargs
                ),
            },
        )


class MultipleSelectWithPop(forms.SelectMultiple):
    def __init__(self, href, *args, **kwargs):
        self.href = href
        self.name = None
        if "name" in kwargs:
            self.name = kwargs.pop("name")

        super(MultipleSelectWithPop, self).__init__(*args, **kwargs)

    def render(self, name, *args, **kwargs):
        return render_to_string(
            "select-with-popup.html",
            {
                "field": self.name or name,
                "href": self.href,
                "select_field": super(MultipleSelectWithPop, self).render(
                    name, *args, **kwargs
                ),
            },
        )


class PopupUtils:
    @staticmethod
    def return_to_parent(pk, obj, obj_repr=""):
        template = """
        <script type="text/javascript">
        opener.dismissAddAnotherPopup(window, "%s", "%s", "%s");
        </script>"""
        return HttpResponse(template % (escape(pk), escape(obj), obj_repr))

    @staticmethod
    def return_to_multiple_parents(pk, obj):
        # template = '''
        # <script type="text/javascript">
        #     opener.$('[id^=' + window.name + ']').each((_,y) =>
        #         opener.dismissAddAnotherPopup({name: y.id, close: () => {}} , "%s", "%s"))
        #     window.close()
        # </script>'''
        template = """
        <script type="text/javascript">
            var common = window.name.substr(0, window.name.lastIndexOf('_')+1)
            var selector = opener.$('[id^=' + common + ']')
            selector.each((_,y) => {
                y.options[y.options.length] = new Option("%(newRepr)s", "%(newId)s", y.id == window.name, y.id == window.name)
            });
            selector.trigger('change')
            window.close()
        </script>"""
        return HttpResponse(template % {"newId": escape(pk), "newRepr": escape(obj)})

    @staticmethod
    def call_parent_continuation():
        template = """
        <script type="text/javascript">
        opener.popupContinuation(window);
        window.close()
        </script>"""
        return HttpResponse(template)
