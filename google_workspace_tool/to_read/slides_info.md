# Introduction

The Google Slides API lets you create and modify Google Slides presentations.

Apps can integrate with the Google Slides API to create beautiful slide decks
automatically from user- and system-provided data. For example, you could
use customer details from a database and combine them with predesigned
templates and selected configuration options to create finished presentations
in a fraction of the time it would take to create them manually.

## Overview of the API

The [presentations](https://developers.google.com/workspace/slides/reference/rest/v1/presentations) collection provides
methods that let you get and update elements within the presentation.

Most of your work with the Slides API will probably be creating and updating
presentations. You'll do this using the
[batchUpdate](https://developers.google.com/workspace/slides/reference/rest/v1/presentations/batchUpdate) method;
this method takes a list of
[Request](https://developers.google.com/workspace/slides/reference/rest/v1/presentations/request#Request)
objects that let you do things like:

- Create slides
- Add elements to slides such as shapes or tables
- Insert, change, and remove text
- Apply transforms to elements
- Change the order of slides

See [Batch updates](https://developers.google.com/workspace/slides/api/guides/overview#batch_updates) for more details. See the Get Started guides
for a simple end-to-end example of how to use the API.

## The structure of a presentation

A presentation in the Slides API is made up of pages, which contain page
elements.

The ID of a presentation can be derived from the URL:

```
https://docs.google.com/presentation/d/presentationId/edit
```

The presentation ID is a string containing letters, numbers, and some special
characters. The following regular expression can be used to extract the
presentation ID from a Google Sheets URL:

```
/presentation/d/([a-zA-Z0-9-_]+)
```

If you're familiar with the Drive API, the `presentationId`
corresponds to the ID of the
[File](https://developers.google.com/workspace/drive/v3/reference/files#resource-representations)
resource.

Pages and page elements are identified by object IDs.

### Pages

Google Slides has the following kinds of pages:

|---|---|
| Masters | Slide masters define the default text styles, background, and page elements that appear in all of the slides that use this master. Page elements that must appear on all slides should be added to the master. Most presentations have one master, but some may have several or none. |
| Layouts | Layouts serve as a template for how page elements will be arranged by default on slides using a layout. Each layout is associated with one master. |
| Slides | These pages contain the content you are presenting to your audience. Most slides are based on a master and a layout. You can specify which layout to use for each slide when it is created. |
| Notes | These pages contain the content for presentation handouts, including a a shape that contains the slide's speaker notes. Each slide has one corresponding notes page. Only the text in the speaker notes shape can be modified with the Slides API. |
| Notes masters | Notes masters define the default text styles and page elements for all notes pages. Notes masters are read-only in the Slides API. |

### Page elements

Page elements are the visual components that are placed on pages. The API
exposes several kinds of page elements:

|---|---|
| Group | A set of page elements that are treated as an individual unit. They can be moved, scaled, and rotated together. |
| Shape | A plain visual object, such as rectangles, ellipses, and text boxes. Shapes can contain text, so they are the most common page elements to build slides. |
| Image | A graphic imported into Slides. |
| Video | A video imported into Slides. |
| Line | A visual line, curve, or connector. |
| Table | A grid of content. |
| WordArt | A visual text element that behaves more like a shape. |
| SheetsChart | A chart imported into Slides from Google Sheets. |

## Batch updates

The [batchUpdate](https://developers.google.com/workspace/slides/reference/rest/v1/presentations/batchUpdate) method
lets you update many aspects of a presentation. Changes are grouped together in
a batch so that if one request fails, none of the other (potentially dependent)
changes are written.

The `batchUpdate` method works by taking one or more
[Request](https://developers.google.com/workspace/slides/reference/rest/v1/presentations/request#Request)
objects, each one specifying a single kind of request to perform. There are
many different kinds of requests. Here's a breakdown of the types of requests,
grouped into different categories.

|---|---|
| Working with Slides: | [CreateSlideRequest](https://developers.google.com/workspace/slides/reference/rest/v1/presentations/request#CreateSlideRequest) |
| Working with Slides: | [UpdateSlidesPositionRequest](https://developers.google.com/workspace/slides/reference/rest/v1/presentations/request#UpdateSlidesPositionRequest) |
| Working with Slides: | [DuplicateObjectRequest](https://developers.google.com/workspace/slides/reference/rest/v1/presentations/request#DuplicateObjectRequest) |
| Working with Slides: | [UpdatePagePropertiesRequest](https://developers.google.com/workspace/slides/reference/rest/v1/presentations/request#UpdatePagePropertiesRequest) |
| Working with Slides: | [DeleteObjectRequest](https://developers.google.com/workspace/slides/reference/rest/v1/presentations/request#DeleteObjectRequest) |
| Working with Page Elements: | [CreateShapeRequest](https://developers.google.com/workspace/slides/reference/rest/v1/presentations/request#CreateShapeRequest) |
| Working with Page Elements: | [CreateLineRequest](https://developers.google.com/workspace/slides/reference/rest/v1/presentations/request#CreateLineRequest) |
| Working with Page Elements: | [UpdatePageElementTransformRequest](https://developers.google.com/workspace/slides/reference/rest/v1/presentations/request#UpdatePageElementTransformRequest) |
| Working with Page Elements: | [UpdateShapePropertiesRequest](https://developers.google.com/workspace/slides/reference/rest/v1/presentations/request#UpdateShapePropertiesRequest) |
| Working with Page Elements: | [DuplicateObjectRequest](https://developers.google.com/workspace/slides/reference/rest/v1/presentations/request#DuplicateObjectRequest) |
| Working with Page Elements: | [DeleteObjectRequest](https://developers.google.com/workspace/slides/reference/rest/v1/presentations/request#DeleteObjectRequest) |
| Working with Tables: | [CreateTableRequest](https://developers.google.com/workspace/slides/reference/rest/v1/presentations/request#CreateTableRequest) |
| Working with Tables: | [InsertTableRowsRequest](https://developers.google.com/workspace/slides/reference/rest/v1/presentations/request#InsertTableRowsRequest) |
| Working with Tables: | [InsertTableColumnsRequest](https://developers.google.com/workspace/slides/reference/rest/v1/presentations/request#InsertTableColumnsRequest) |
| Working with Tables: | [DeleteTableRowRequest](https://developers.google.com/workspace/slides/reference/rest/v1/presentations/request#DeleteTableRowRequest) |
| Working with Tables: | [DeleteTableColumnRequest](https://developers.google.com/workspace/slides/reference/rest/v1/presentations/request#DeleteTableColumnRequest) |
| Working with Tables: | [UpdateTableRowPropertiesRequest](https://developers.google.com/workspace/slides/reference/rest/v1/presentations/request#UpdateTableRowPropertiesRequest) |
| Working with Tables: | [UpdateTableColumnPropertiesRequest](https://developers.google.com/workspace/slides/reference/rest/v1/presentations/request#UpdateTableColumnPropertiesRequest) |
| Working with Tables: | [UpdateTableBorderPropertiesRequest](https://developers.google.com/workspace/slides/reference/rest/v1/presentations/request#UpdateTableBorderPropertiesRequest) |
| Working with Tables: | [UpdateTableCellPropertiesRequest](https://developers.google.com/workspace/slides/reference/rest/v1/presentations/request#UpdateTableCellPropertiesRequest) |
| Working with Tables: | [MergeTableCellsRequest](https://developers.google.com/workspace/slides/reference/rest/v1/presentations/request#MergeTableCellsRequest) |
| Working with Tables: | [UnmergeTableCellsRequest](https://developers.google.com/workspace/slides/reference/rest/v1/presentations/request#UnmergeTableCellsRequest) |
| Working with Tables: | [DeleteObjectRequest](https://developers.google.com/workspace/slides/reference/rest/v1/presentations/request#DeleteObjectRequest) |
| Working with Charts: | [CreateSheetsChartRequest](https://developers.google.com/workspace/slides/reference/rest/v1/presentations/request#CreateSheetsChartRequest) |
| Working with Charts: | [RefreshSheetsChartRequest](https://developers.google.com/workspace/slides/reference/rest/v1/presentations/request#RefreshSheetsChartRequest) |
| Working with Charts: | [ReplaceAllShapesWithSheetsChartRequest](https://developers.google.com/workspace/slides/reference/rest/v1/presentations/request#ReplaceAllShapesWithSheetsChartRequest) |
| Working with Charts: | [DeleteObjectRequest](https://developers.google.com/workspace/slides/reference/rest/v1/presentations/request#DeleteObjectRequest) |
| Working with Images and Video: | [CreateImageRequest](https://developers.google.com/workspace/slides/reference/rest/v1/presentations/request#CreateImageRequest) |
| Working with Images and Video: | [CreateVideoRequest](https://developers.google.com/workspace/slides/reference/rest/v1/presentations/request#CreateVideoRequest) |
| Working with Images and Video: | [UpdateImagePropertiesRequest](https://developers.google.com/workspace/slides/reference/rest/v1/presentations/request#UpdateImagePropertiesRequest) |
| Working with Images and Video: | [UpdateVideoPropertiesRequest](https://developers.google.com/workspace/slides/reference/rest/v1/presentations/request#UpdateVideoPropertiesRequest) |
| Working with Images and Video: | [ReplaceAllShapesWithImageRequest](https://developers.google.com/workspace/slides/reference/rest/v1/presentations/request#ReplaceAllShapesWithImageRequest) |
| Working with Images and Video: | [DuplicateObjectRequest](https://developers.google.com/workspace/slides/reference/rest/v1/presentations/request#DuplicateObjectRequest) |
| Working with Images and Video: | [DeleteObjectRequest](https://developers.google.com/workspace/slides/reference/rest/v1/presentations/request#DeleteObjectRequest) |
| Working with Text: | [InsertTextRequest](https://developers.google.com/workspace/slides/reference/rest/v1/presentations/request#InsertTextRequest) |
| Working with Text: | [DeleteTextRequest](https://developers.google.com/workspace/slides/reference/rest/v1/presentations/request#DeleteTextRequest) |
| Working with Text: | [ReplaceAllTextRequest](https://developers.google.com/workspace/slides/reference/rest/v1/presentations/request#ReplaceAllTextRequest) |
| Working with Text: | [CreateParagraphBulletsRequest](https://developers.google.com/workspace/slides/reference/rest/v1/presentations/request#CreateParagraphBulletsRequest) |
| Working with Text: | [DeleteParagraphBulletsRequest](https://developers.google.com/workspace/slides/reference/rest/v1/presentations/request#DeleteParagraphBulletsRequest) |
| Working with Text: | [UpdateTextStyleRequest](https://developers.google.com/workspace/slides/reference/rest/v1/presentations/request#UpdateTextStyleRequest) |
| Working with Text: | [UpdateParagraphStyleRequest](https://developers.google.com/workspace/slides/reference/rest/v1/presentations/request#UpdateParagraphStyleRequest) |

The `batchUpdate` method returns a [response body](https://developers.google.com/workspace/slides/reference/rest/v1/presentations/batchUpdate#response-body),
which contains a
[Response](https://developers.google.com/workspace/slides/reference/rest/v1/presentations/request#Response)
for each request. Each response occupies the same index as the corresponding
request; for requests with no applicable response, the response at that index
will be empty. The various `Create` requests normally do have responses, so
that you know the ID of the newly added object.

## Working with object IDs

A presentation in the Slides API is made up of *pages* and
*page elements* . These objects include an *object ID* string that is unique
within a presentation.

### Specifying object IDs on creation

When creating pages or page elements using the
[batchUpdate](https://developers.google.com/workspace/slides/reference/rest/v1/presentations/batchUpdate)
method, you can optionally specify an object ID for the new object. This lets
you create an object and modify it within the same batchUpdate request,
minimizing the number of calls to the Slides API and reducing  

[quota usage](https://developers.google.com/workspace/slides/limits).

> [!NOTE]
> Object IDs must be between 5 and 50 characters in length.  
> They must start with an alphanumeric character (\[a-zA-Z0-9\]) or an underscore ("_"). Subsequent characters can be alphanumeric, or underscores, dashes ("-"), or colons (":").

We recommend generating a random object ID in most cases. For example, if you
are using Java, `java.util.UUID.randomUUID().toString()` should work well.

When your application wants to keep track of objects over a longer period of
time, don't rely on the object ID, because it may change. See the following
section for more details.

### Keeping track of objects without using the object ID

When you make a Slides API request, the object ID is normally
preserved. (Any exceptions are called out in the method's reference
documentation.) Making a copy of a whole presentation with the
Drive API also preserves object IDs.

However, you cannot depend on an object ID being unchanged after a presentation
is changed in the Slides UI. For example, if someone uses the
Slides UI to copy-paste a page element and then deletes the
original, the page element will now have a new unique ID, and the ID you
previously provided through the API will be lost. As a result, we don't
recommend you store object IDs in your application's storage. Instead, you
should find objects in the presentation by its text content or alt-text.

> [!WARNING]
> Some actions, especially manual editing using the Slides UI, can change object IDs. So in the longer term, keep track of objects using their text content.

Newly created presentations normally use a consistent set of IDs for default
slides, masters, and text boxes. These IDs are subject to change over time,
so we don't recommend that you rely on this feature. Instead, find the elements
you'd like to modify using the presentation object returned by calls to
[create()](https://developers.google.com/workspace/slides/reference/rest/v1/presentations/create) or
[get()](https://developers.google.com/workspace/slides/reference/rest/v1/presentations/get).


# Pages, Page Elements, and Properties

In order to use the Slides API effectively, you need to understand the
architecture of presentations and their components, as well as the interactions
between these components. This page provides a detailed overview of these
topics:

- Conceptual model of the presentation components
- How the API represents these components
- Styling properties of the components

Reading this and the other concept overviews will make it easier to understand
and use the [how-to guides](https://developers.google.com/workspace/slides/how-tos/create-slide), the
[reference documentation](https://developers.google.com/workspace/slides/reference/rest), and the [recipe
samples](https://developers.google.com/workspace/slides/samples).

## Presentations, pages, and page elements

The outermost container element in Google Slides is a *presentation*. This is
the unit that can be saved in Google Drive, shared with other users, and so on.

Each presentation contains various types of *pages* . One type of page is a
a *slide*, which are the pages that users
see and flip between when the presentation is rendered on a screen.

Each page contains a number of *page elements*, which together make up the
content of the page, as shown in the following diagram:

![slides architecture diagram](https://developers.google.com/static/workspace/slides/api/images/presentation-architecture.png)

Aside from slides, there are other types of pages that let you apply design to
many different slides, creating a consistent appearance. These other page types
are *masters* and *layouts*, and their properties determine the way slides are
rendered, as shown in the following diagram:

![themes architecture diagram](https://developers.google.com/static/workspace/slides/api/images/theme-architecture.png)

**Masters** --- The master page serves two purposes. Placeholders on the master
contain the defaults for text styles used throughout your presentation. The
background and any other shapes on the master slide make up the default
background for all slides based on that master. If you have a company logo that
you want to appear on every slide in your presentation, put it on the master.

**Layouts** --- Layout templates determine how content is arranged on each type
of slide. If you'd like all your title slides to look a certain way, for
example, you might edit the title layout template.

There are two more kinds of pages---notes pages and notes
masters---which are mostly relevant for working with [speaker
notes](https://developers.google.com/workspace/slides/how-tos/notes).

## API representation types and structure

This section describes how the Google Slides conceptual model, as described
above, is represented in the Slides API.

The following diagram depicts the relationship between presentations, pages,
and page elements as types in the Slides API:

![erd-style diagram](https://developers.google.com/static/workspace/slides/api/images/hierarchy-erd.png)

The following sections show how these types are represented in JSON.

### Presentations

A [presentation](https://developers.google.com/workspace/slides/reference/rest/v1/presentations) includes a
number of properties, and contains the pages that are in it:

    {
      "presentationId": string,
      "pageSize": { object(Size) },
      "slides": [ { object(Page) } ],
      "title": string,
      "masters": [ { object(Page) } ],
      "layouts": [ { object(Page) } ],
      "notesMaster": object(Page),
      "locale": string,
    }

### Pages

A [page](https://developers.google.com/workspace/slides/reference/rest/v1/presentations.pages#Page) includes
a set of properties, and contains the page elements that are in it:

    {
      "objectId": string,
      "pageType": enum(PageType),
      "pageElements": [ { object(PageElement) } ],
      "pageProperties": { object(PageProperties) },

      // Union field properties can be only one of the following:
      "slideProperties": { object(SlideProperties) },
      "layoutProperties": { object(LayoutProperties) },
      "notesProperties": { object(NotesProperties) },
      // End of list of possible types for union field properties.
    }

### Page elements

[Page elements](https://developers.google.com/workspace/slides/reference/rest/v1/presentations.pages#Page.PageElement)
are the visual components that are placed on pages. A page element in the API
contains a number of properties, including a field that varies according to the
kind of page element:

    {
      "objectId": string,
      "size": { object(Size) },
      "transform": { object(AffineTransform) },
      "title": string,
      "description": string,

      // Union field element_kind can be only one of the following:
      "elementGroup": { object(Group) },
      "shape": { object(Shape) },
      "image": { object(Image) },
      "video": { object(Video) },
      "line": { object(Line) },
      "table": { object(Table) },
      "wordArt": { object(WordArt) },
      "sheetsChart": { object(SheetsChart) },
      // End of list of possible types for union field element_kind.
    }

There are several kinds of page elements, as defined by the union field
`element_kind` in the definition above. These kinds of page elements are
described in the following table:

| Element kind | Description |
|---|---|
| Group | A set of page elements that are treated as an individual unit. They can be moved, scaled, and rotated together. |
| Shape | A plain visual object, such as rectangles, ellipses, and text boxes. Shapes can contain text, so they are the most common page elements to build slides. |
| Image | A graphic imported into Slides. |
| Video | A video imported into Slides. |
| Line | A visual line, curve, or connector. |
| Table | A grid of content. |
| WordArt | A visual text element that behaves more like a shape. |
| SheetsChart | A chart imported into Slides from Google Sheets. |

## Page and page element properties

The Slides API lets you read and update the appearance of pages and page
elements in your presentation. Different page elements support
different properties that control how the page element is rendered.

Each page element kind has a corresponding properties element and a properties
update message, for example:

- There is a page element type [Shape](https://developers.google.com/workspace/slides/reference/rest/v1/presentations.pages/shapes#Page.Shape)
- Its property field is [shapeProperties](https://developers.google.com/workspace/slides/reference/rest/v1/presentations.pages/shapes#Page.ShapeProperties)
- The request to update these properties is [UpdateShapePropertiesRequest](https://developers.google.com/workspace/slides/reference/rest/v1/presentations/request#updateshapepropertiesrequest)

The same set of element/properties/update request exists for each page element
type:
[Image](https://developers.google.com/workspace/slides/reference/rest/v1/presentations.pages/images#Page.Image)/[imageProperties](https://developers.google.com/workspace/slides/reference/rest/v1/presentations.pages/images#Page.ImageProperties)/[UpdateImageProperties](https://developers.google.com/workspace/slides/reference/rest/v1/presentations/request#updateimagepropertiesrequest),
and so on.

You can read the properties attribute wherever you encounter it on reading an
element; to change the values in it, use it with the matching request type as
the payload for the
[batchUpdate](https://developers.google.com/workspace/slides/reference/rest/v1/presentations/batchUpdate)
method, letting you change these values in the presentation.

### Kinds of properties

There are some properties that are common between several kinds of objects in
the Slides API:

| Property | Description |
|---|---|
| Color | Colors in the Slides API can be either an RGB value or a reference to a theme color. Theme colors are referred to by name (for example "DARK1") and can be mapped to RGB values using a [page's color scheme](https://developers.google.com/workspace/slides/reference/rest/v1/presentations.pages#Page.ColorScheme). This color scheme is commonly updated when changing the theme of the presentation in the Slides editor. |
| Fill | The fill represents the rendering of empty space inside an object. The most commonly supported fill in Slides is a solid fill, where the interior of an object is filled with a single solid color. Fills can also be used for the backgrounds of Pages. |
| Outline | The outline represents the set of lines that surround the page element. The color of lines is controlled with a Fill. Callers can also adjust the width and dash style of outlines. |
| Shadow | The shadow represents a visual effect meant to mimic a physical shadow cast by the object. Currently, shadows in the Slides API are read only. |

### Updating properties

To update a property, use the appropriate
`Update...Properties` request in a
[batchUpdate](https://developers.google.com/workspace/slides/reference/rest/v1/presentations/batchUpdate)
call---for example, `UpdateShapeProperties` for Shapes. These requests
accept a full properties message and can use
[field masks](https://developers.google.com/workspace/slides/how-tos/field-masks) to determine which fields in the
properties message should be updated.

> [!NOTE]
> A field mask of `*` is treated like a wildcard and is shorthand for specifying every field in a message. The wildcard syntax can produce unwanted results if the API is updated in the future, as read-only fields and newly added fields may cause errors. For production applications, always list the specific fields being updated in field masks and avoid using `*` wildcards.

## Property inheritance

A page or page element can inherit properties from its parent objects. An
object's properties, including those that it defines and those that it
inherits, determine its final visual appearance.

- **Page properties** --- A page inherits any properties that it doesn't define, but that are defined in the layout or master on which it is based.
- **Shape properties** --- A shape can be marked as a *placeholder*, letting you explicitly reference another placeholder shape (on the page's parent layout or master) from which to inherit properties.

These ideas are explained further in the following paragraphs.

### Inheritance of page properties

The structure of slides, layouts, and masters define an inheritance hierarchy
within the presentation: slides inherit from layouts, and layouts inherit from
masters. A slide's parent layout and master are specified in the slide's
[slideProperties](https://developers.google.com/workspace/slides/reference/rest/v1/presentations.pages#Page.SlideProperties)
field.

A page can inherit properties, such as
[background](https://developers.google.com/workspace/slides/reference/rest/v1/presentations.pages#Page.PageBackgroundFill)
and [color
scheme](https://developers.google.com/workspace/slides/reference/rest/v1/presentations.pages#Page.ColorScheme),
from a parent page. To inherit a property, the child page simply doesn't set a
value for that property in its
[PageProperties](https://developers.google.com/workspace/slides/reference/rest/v1/presentations.pages#Page.PageProperties)
message. By not "overriding" the value defined by the parent, the page accepts
the inherited value.

The following diagram shows a slide inheriting properties from a layout, which
inherits from a master:

![slide property inheritance](https://developers.google.com/static/workspace/slides/api/images/prop-inheritance.png)

The properties used to render a slide are a combination of those it defines and
those it inherits. In this example the resolved values used to render Slide 1
are as follows:

- PropertyA is "Red".
- PropertyB is "Orange".

> [!NOTE]
> A page that has no parent inherits default values from Google Slides for any properties that it doesn't define.

### Inheritance of shape properties

Shapes can inherit properties, such as fill, outline, or shadow, from other
shapes. A shape is a *placeholder* if its
[Shape.placeholder](https://developers.google.com/workspace/slides/reference/rest/v1/presentations.pages/shapes#Page.Placeholder)
field is set. The child placeholder's `Shape.placeholder.parentObjectId` field
identifies its parent placeholder. When you create a new slide based on a
layout, any placeholders in that layout appear as child shapes in the new slide.
Similarly, placeholders on master pages can act as parents of the layouts'
placeholders.

> [!NOTE]
> You can create placeholders using the Slides editor, but not using the Slides API.

With this inheritance hierarchy defined, the child page inherits a property by
not setting that value in its
[ShapeProperties](https://developers.google.com/workspace/slides/reference/rest/v1/presentations.pages#shapeproperties)
message. By not overriding the value defined by the parent, the child shape
accepts the inherited value.

The following diagram shows the inheritance of properties between three
placeholders that are contained in a slide, a layout, and a master:

![shape property inheritance](https://developers.google.com/static/workspace/slides/api/images/placeholder-inheritance.png)

The properties used to render a placeholder shape are a combination of those it
defines and those it inherits. In this example the resolved values for these
shapes are as follows:

- Placeholder1: propertyA is rendered as "Yellow", propertyB as "Green".
- Placeholder2: propertyA is rendered as "Yellow", propertyB as "Purple".
- Placeholder3: propertyA is rendered as "Yellow", propertyB as "Purple".

Shapes are the only type of page element that can have parents. Other types,
such as images, tables, and charts, cannot be placeholders and cannot have
parents.

> [!NOTE]
> When a shape has no parent, any values that it doesn't set are automatically populated with application defaults.

### "Hiding" properties using PropertyState

The
[PropertyState](https://developers.google.com/workspace/slides/reference/rest/v1/presentations.pages#propertystate)
enumeration controls whether a shape's property is actually used for rendering,
or if the value is only used for inheritance by child shapes. A property with
property state `NOT_RENDERED` will not be used when rendering the shape on its
page, however children that have a corresponding property state of `RENDERED`
can still inherit this property.

> [!NOTE]
> Updating a property implicitly sets its property state to \`RENDERED\`, unless you set the state to some other value in the same request.

The following diagram shows the inheritance of properties between three
placeholders that manipulate the PropertyState field:

![property state and inheritance](https://developers.google.com/static/workspace/slides/api/images/propertystate-inheritance.png)

The rendering of shape properties can be affected by the PropertyState field.
In this example the resolved values for these shapes are as follows:

- Placeholder1: propertyA is rendered as "Red".
- Placeholder2: propertyA is not rendered. If this was the outline property, Placeholder2 would have no outline.
- Placeholder3: propertyA is not rendered.

There's one more possible value of The
[PropertyState](https://developers.google.com/workspace/slides/reference/rest/v1/presentations.pages#propertystate)
enumeration: the `INHERIT` property state means that the property state itself
is inherited, and the parent's value should be used. Shapes with no parents
cannot have a property state of `INHERIT`.


# Text Structure and Styling

In the Slides API, text can be contained in shapes or in table cells.
Before you can manipulate and style text, you need to understand its structure
and how styling works.

This page describes how text is represented in the Slides API.

## Sequences of text elements

The text contained in a shape or a table cell is made up of a sequence of
[TextElement](https://developers.google.com/workspace/slides/reference/rest/v1/presentations.pages#textelement)
structures. This sequence represents the structure of text, in the order it
appears from start to end.

For example, consider the contents of this slide---all contained in one
text box:

![screenshot of a simple slide](https://developers.google.com/static/workspace/slides/api/images/text-seq-slide.png)

The slide above has one text box, whose `text` field contains a sequence
of text elements as shown in the following diagram:

![diagram showing a sequence of text elements](https://developers.google.com/static/workspace/slides/api/images/text-elements.png)

More concretely, this sequence of text is represented in the Slides API as
follows:

    "textElements": [ {
        "endIndex": 224,
        "paragraphMarker": { "style": {} }
      }, {
        "endIndex": 130,
        "textRun": { "content": "Li lingues differe in li grammatica e li vocabules. Omnicos directe al desirabilite de un nov ", "style": {} }
      }, {
        "endIndex": 143,
        "startIndex": 130,
        "textRun": { "content": "lingua franca", "style": { "italic": True } }
      }, {
        "endIndex": 224,
        "startIndex": 143,
        "textRun": { "content": ": solmen va esser necessi far:\n", "style": {} }
      }, {
        "endIndex": 243,
        "startIndex": 224,
        "paragraphMarker": {
          "style": { "indentStart": { "magnitude": 36, "unit": "PT" }, "direction": "LEFT_TO_RIGHT", "indentFirstLine": { "magnitude": 18, "unit": "PT" }, "spacingMode": "COLLAPSE_LISTS" },
          "bullet": { "listId": "foo123", "glyph": "\u25cf" }
        }
      }, {
        "endIndex": 243,
        "startIndex": 224,
        "textRun": { "content": "uniform grammatica\n", "style": {} }
      }, {
        "endIndex": 257,
        "startIndex": 243,
        "paragraphMarker": {
            "style": { "indentStart": { "magnitude": 36, "unit": "PT" }, "direction": "LEFT_TO_RIGHT", "indentFirstLine": { "magnitude": 18, "unit": "PT" }, "spacingMode": "COLLAPSE_LISTS" },
            "bullet": { "listId": "foo123", "glyph": "\u25cf" }
        }
    }, {
        "endIndex": 257,
        "startIndex": 243,
        "textRun": { "content": "Pronunciation\n", "style": {} }
    }, {
        "endIndex": 277,
        "startIndex": 257,
        "paragraphMarker": {
            "style": { "indentStart": { "magnitude": 36, "unit": "PT" }, "indentFirstLine": { "magnitude": 18, "unit": "PT" }, "spacingMode": "COLLAPSE_LISTS" },
            "bullet": { "listId": "foo123", "glyph": "\u25cf" }
        }
    }, {
        "endIndex": 277,
        "startIndex": 257,
        "textRun": { "content": "plu sommun paroles.\n", "style": {} }
    }, {
        "endIndex": 500,
        "startIndex": 277,
        "paragraphMarker": { "style": {} }
    }, {
        "endIndex": 500,
        "startIndex": 277,
        "textRun": { "content": "Ka swu thefognay, tay waddeant varpa u inzo.\n", "style": {} }
    }]

### TextElement contents

Each text element contains a zero-based **start index** and **end index**,
which describe the element's location within the full text of the page
element, together with one of the following types of text object:

| Text kind | Description |
|---|---|
| ParagraphMarker | This text element represents the start of a new paragraph. The start and end index of the text element represents the full span of the paragraph, including the newline character that ends the paragraph. A paragraph never overlaps another paragraph. Paragraphs always end in a newline character, so there's always a newline at the end of the text contents of a shape or table cell. Paragraphs can belong to bulleted or numbered lists. If so, the `ParagraphMarker.bullet` field contents include a list ID. This ID references a list element that exists inside `TextContent` alongside the `TextElement` sequence. Paragraphs within the same logical list will refer to the same list ID. |
| TextRun | This text element represents a contiguous string of text that all has the same text style. Text runs never cross paragraph boundaries: even if the text ending one paragraph has the same styling as the text starting the next paragraph, the contents are split after the newline character to form separate text runs. If you need to process the full text string inside a page element, iterate through all of the text elements, concatenating the strings found in all of the text runs. |
| AutoText | Autotext refers to places in the text that change dynamically depending on the context. In Slides, this is used to represent the current slide number inside text. |

The Slides API may include more text element kinds in the future. Code that processes text should handle new text element kinds gracefully, typically by ignoring unknown kinds.

## Modifying text contents

When you need to modify text using the Slides API, you don't have to explicitly
create all the appropriate text elements. Instead, you can operate on text like
you would in the Slides editor: by inserting text, deleting ranges, and
updating styles on ranges. These operations implicitly create `ParagraphMarker`
and `TextRun` elements as needed to reflect your changes.

### Inserting text

You can insert text at an index using the
[InsertTextRequest](https://developers.google.com/workspace/slides/reference/rest/v1/presentations/request#inserttextrequest)
request in a call to
[batchUpdate](https://developers.google.com/workspace/slides/reference/rest/v1/presentations/batchUpdate). This
method's `insertionIndex` field specifies where to insert the text; you can
compute this index using the start and end index fields inside text elements.

Text insertion has some side effects that mirror the behavior of the Slides
editor:

- Inserting a newline character implicitly creates a new paragraph, creating a `ParagraphMarker` text element that starts at the newline's index and ends at the following newline. The paragraph style---including bullet and list details --- is copied from the current paragraph to the new paragraph.
- The style of inserted characters is determined automatically, generally keeping the same text style that existed at the insertion index. As a result, the text is generally inserted into the existing `TextRun` at that index. You can update this style later using an [UpdateTextStyle](https://developers.google.com/workspace/slides/reference/rest/v1/presentations/request#updatetextstylerequest) request.

### Deleting text

You can delete a range of text using the
[DeleteTextRequest](https://developers.google.com/workspace/slides/reference/rest/v1/presentations/request#deletetextrequest)
message in a call to
[batchUpdate](https://developers.google.com/workspace/slides/reference/rest/v1/presentations/batchUpdate).
Deleting text involves some subtleties:

- A deletion that crosses a paragraph boundary merges the two paragraphs, deleting the separating `ParagraphMarker` text element.
- The new merged paragraph will use a combined paragraph style, matching behavior in the Slides editor.
- A deletion whose range encompasses a text run removes all the content from a text run, and it also deletes the text run itself.
- A deletion whose range encompasses an `AutoText` element deletes the `AutoText` element.

## Updating text style

The rendered appearance of text in a slide is determined by text style
properties:

- **Paragraph styles** like indention, alignment, and bullet glyphs, are defined by properties on paragraph markers.
- **Character styles** like bold, italic, and underline, are defined by properties on individual text runs.

### Updating character style

You can update character styles using the [UpdateTextStyleRequest](https://developers.google.com/workspace/slides/reference/rest/v1/presentations/request#updatetextstylerequest)
message in a call to
[batchUpdate](https://developers.google.com/workspace/slides/reference/rest/v1/presentations/batchUpdate).

Like other text operations, the character style is applied to a range
of text and implicitly creates new `TextRun` objects as needed.

Setting some character styles implicitly updates other related styles to match
the behavior in the Slides editor. For example, adding a link automatically
changes the text foreground color and underline properties. See
the [TextStyle](https://developers.google.com/workspace/slides/reference/rest/v1/presentations.pages/text#Page.TextStyle)
reference documentation for more details.

### Updating paragraph style

You can update paragraph styles using the
[UpdateParagraphStyleRequest](https://developers.google.com/workspace/slides/reference/rest/v1/presentations/request#updateparagraphstylerequest)
message in a call to
[batchUpdate](https://developers.google.com/workspace/slides/reference/rest/v1/presentations/batchUpdate).

The Slides API supports a
[CreateParagraphBulletsRequest](https://developers.google.com/workspace/slides/reference/rest/v1/presentations/request#createparagraphbulletsrequest)
that mirrors the functionality of the bullet presets in the Slides editor for
creation of bulleted and numbered lists. Similarly,
[DeleteParagraphBulletsRequest](https://developers.google.com/workspace/slides/reference/rest/v1/presentations/request#deleteparagraphbulletsrequest)
removes any existing bullets on paragraphs.

## Inherited styles

Some shapes, known as *placeholders* , can inherit text styles from other, parent
shapes: see [placeholders](https://developers.google.com/workspace/slides/concepts/page-elements#inheritance_of_shape_properties)
to learn more about shape inheritance in general.

This section focuses on how style inheritance works to create the final,
rendered text styles displayed in a slide.

### Style representation in placeholders

The section on
[placeholders](https://developers.google.com/workspace/slides/concepts/page-elements#inheritance_of_shape_properties)
describes how inheritance works between parent and child shapes. Inheritance of
text styles is handled by additional features within the inheritance model:

- Properties of [ParagraphMaker](https://developers.google.com/workspace/slides/reference/rest/v1/presentations.pages/text#Page.ParagraphMarker) text elements define paragraph formatting.
- Properties of [TextRun](https://developers.google.com/workspace/slides/reference/rest/v1/presentations.pages/text#Page.TextRun) text elements define character formatting.
- The content of parent placeholders contains eight such ParagraphMarker/TextRun pairs (to support eight levels of list nesting).
- A child placeholder inherits its default text properties from these text elements in its parent's text content.

The following diagram shows one way to visualize these relationships:

![diagram of child shape inheriting text properties](https://developers.google.com/static/workspace/slides/api/images/text-style-inheritance.png)

The first ParagraphMarker/TextRun in the parent shape determines most of the
inherited text styling; the styling in the remaining seven pairs only affects
paragraphs at progressively more-deeply nested bullet levels:

| Parent text element pair | Child formatting that it controls |
|---|---|
| First `ParagraphMarker` First `TextRun` | Text style of level 0 (outermost) list paragraphs and all non-list paragraphs. |
| Second `ParagraphMarker` Second `TextRun` | Text style of remaining (nested) list levels 1-7 |
| Third `ParagraphMarker` Third `TextRun` | Text style of remaining (nested) list levels 1-7 |
| Fourth `ParagraphMarker` Fourth `TextRun` | Text style of remaining (nested) list levels 1-7 |
| Fifth `ParagraphMarker` Fifth `TextRun` | Text style of remaining (nested) list levels 1-7 |
| Sixth `ParagraphMarker` Sixth `TextRun` | Text style of remaining (nested) list levels 1-7 |
| Seventh `ParagraphMarker` Seventh `TextRun` | Text style of remaining (nested) list levels 1-7 |
| Eighth `ParagraphMarker` Eighth `TextRun` | Text style of remaining (nested) list levels 1-7 |

To access these pairs of text elements, use their explicit index within the
`textElements` field as shown in the snippet below, which shows setting the
default (inheritable) styling for level 0 and non-list paragraphs:

    "text": {
      "textElements": [  {
         "startIndex": 0,
         "endIndex": 1,
         "paragraphMarker": {
           "style": {  "alignment": "START",  ...  },
           "bullet": {  "nestingLevel": 0,  ...  }
         }
       },{
         "startIndex": 0,
         "endIndex": 1,
         "textRun": {
           "content": "\n",
           "style": {  "foregroundColor": {  "opaqueColor": {  "themeColor": "DARK1"  }  },  }
         }
       },{
         ...
       } ]
     }

Note that the `content` field of a parent shape's `TextRun` always consists of
a single newline character.

### Inherited styles can be overridden

A child shape can specify styling properties on the
[ParagraphMarker](https://developers.google.com/workspace/slides/reference/rest/v1/presentations.pages#paragraphmarker)
and [TextRun](https://developers.google.com/workspace/slides/reference/rest/v1/presentations.pages#textrun)
elements in its content. These locally specified properties will override any
inherited properties within their local scope. Elements that do not specify any
style will use the corresponding style inherited from the parent.

Removing an explicit style property from a child shape, so that it is no longer
set, will cause it to inherit from the parent.

### Example

Given the inheritance shown in the diagram above, suppose the shape
`ParentPlaceholder` has the following text content:

    "text": {
      "textElements": [
        { "startIndex": 0,  "endIndex": 1,
          "paragraphMarker": {
            "style": {"alignment": "START", ...},
            "bullet": {"nestingLevel": 0, ...}
          }
        },
        { "startIndex": 0,  "endIndex": 1,
          "textRun": {
            "content": "\n",
            "style": {"foregroundColor": {"opaqueColor": {"themeColor": "DARK1"} }, }
            ...
          }
        },
        { "startIndex": 1,  "endIndex": 2,
          "paragraphMarker": {
            "style": {"alignment": "END", ...},
            "bullet": {"nestingLevel": 1, ...}
          }
        },
        { "startIndex": 1,  "endIndex": 2,
          "textRun": {
            "content": "\n",
            "style": {"foregroundColor": {"opaqueColor": {"themeColor": "LIGHT1"} }, ...}
          }
        },
       ...
      ]
    }

And suppose the shape `ChildPlaceholder` has the following text content:

    "text": {
      "textElements": [
        { "startIndex": 0,  "endIndex": 1,
          "paragraphMarker": {
            "style": {},
          }
        },
        { "startIndex": 0,  "endIndex": 1,
          "textRun": {
            "content": "This is my first paragraph\n",
            "style": {},
          }
          ...
        },
        {  "startIndex": 1,  "endIndex": 2,
          "paragraphMarker": {
            "style": {},
            "bullet": {
              "nestingLevel": 1,
              "listId": "someListId",
              "glyph": "●"
            }
          }
        },
        { "startIndex": 1,  "endIndex": 2,
          "textRun": {
            "content": "This paragraph is in a list\n",
            "style": {},
            ...
          }
        }
      ]
    }

This results in the outcomes described in the following paragraphs.

#### Style inheritance for a plain paragraph

The child shape's first paragraph, which includes the
text "This is my first paragraph", is a plain paragraph (not in a list).
No elements in its text content specify any style properties,
so it inherits all its character and paragraph styles from
its parent. This causes the following rendering:

- Text: "This is my first paragraph" is the text rendered. Text itself is never inherited.
- Alignment: The text is rendered with `START` alignment, inherited from the parent's first `ParagraphMarker`.
- Foreground color: The text is rendered with `DARK1` foreground color, inherited from the parent's first `TextRun`.

#### Style inheritance for a list paragraph

The next paragraph, which includes the text "This paragraph is in a list", is in
a bulleted list at nesting level 1, since its corresponding `ParagraphMarker`
has its `bullet` field set to this level. As a result, it inherits text and
paragraph style from nesting level 1 in the parent. This results in the
following rendering:

- Text: "This paragraph is in a list" is the text rendered. Text itself is never inherited.
- Alignment: The text is rendered with "END" alignment, inherited from the parent's second `ParagraphMarker`.
- Foreground color: The text is rendered with `LIGHT1` text foreground color, inherited from the parent's second `TextRun`.

### Interactions between updating and inheriting text and paragraph styles

Text styles that aren't set in a child shape will inherit values from its
parent. Text styles that are set in the child will "override" the parent values
in some local scope.

You can use an [UpdateTextStyleRequest](https://developers.google.com/workspace/slides/reference/rest/v1/presentations/request#updatetextstylerequest)
to unset a child shape's text style, so that it no longer has a local override
and thus inherits its syltes from the parent shape. Additionally, updating the
child's text style to match the value inherited from a parent
**implicitly unsets the style** so that it uses the inherited value.

> [!NOTE]
> Setting a child shape's style to exactly match that of its parent (causing it to inherit the style) has an identical outcome to unsetting the child shape's style.

This does not affect the visual
appearance of text immediately after an update, but it may matter if you later
update a paragraph or text style in a parent placeholder. This inheritance
behavior matches the behavior of the Slides editor, so you can experiment with
the outcomes of style changes before working against the API.

### Example

Consider the definitions in the [previous example](https://developers.google.com/workspace/slides/api/concepts/text#PlainAndListExample) for
`ChildPlaceholder` and `ParentPlaceholder`.

Now suppose you submit this [UpdateTextStyleRequest](https://developers.google.com/workspace/slides/reference/rest/v1/presentations/request#updatetextstylerequest):

    { "updateTextStyle": {
        "objectId": "ChildPlaceholder",
        "style": {"foregroundColor": {"opaqueColor": {"themeColor": "DARK1"} }, },
        "textRange": { "type": "ALL" },
        "fields": "foregroundColor"
      }
    }

This request attempts to set a `DARK1` foregroundColor to all of
ChildPlaceholder's text, using a [field mask](https://developers.google.com/workspace/slides/how-tos/field-masks)
to specify that only the foreground color of the element should change. This
request has the following outcomes:

- First paragraph: The new `foregroundColor` matches the inherited `foregroundColor`, so this style is unchanged and still inherits.
- Second paragraph: The new `foregroundColor` doesn't match the inherited `foregroundColor`, so the second paragraph's foreground color is updated to `DARK1`.

ChildPlaceholder's text content is now:

    "text": {
      "textElements": [
        { "startIndex": 0,  "endIndex": 1,
          "paragraphMarker": {
            "style": {},
          }
        },
        { "startIndex": 0,  "endIndex": 1,
          "textRun": {
            "content": "This is my first paragraph\n",
            "style": {},
          }
          ...
        },
        { "startIndex": 1,  "endIndex": 2,
          "paragraphMarker": {
            "style": {},
            "bullet": {"nestingLevel": 1, "listId": "someListId", "glyph": "●" }
          }
        },
        { "startIndex": 1,  "endIndex": 2,
          "textRun": {
            "content": "This paragraph is in a list\n",
            "style": {"foregroundColor": {"opaqueColor": {"themeColor": "DARK1"} }, },
            ...
          }
        }
      ]
    }

## Bullet glyph text style

Like normal text, bullet glyphs have a text style that controls how the glyph
is rendered. These text styles cannot be modified using the Slides API
directly. However, if you use an
[UpdateTextStyleRequest](https://developers.google.com/workspace/slides/reference/rest/v1/presentations/request#updatetextstylerequest)
to update a complete paragraph that includes a bullet, the Slides API updates
the bullet glyph's text style to match.

Bullet glyph text styles follow a slightly different inheritance hierarchy than
normal text styles.

1. A bullet at a certain nesting level first inherits from the `TextStyle` set in the `NestingLevel.bullet_style` field inside the bullet's `List` object.
2. It next inherits from the corresponding `NestingLevel.bullet_style` in its parent placeholder's `List`.
3. Finally it seeks to inherit from remaining parent placeholder objects.

Bullets in placeholders on Layouts and Masters have an empty \`TextStyle\` in the \`ParagraphMarker.bullet\` field, and any text style updates on those paragraphs will only be reflected in the \`List\` messages.



# Transforms and Page Elements

This guide describes the underlying concepts used in transforming (that is,
moving, rotating, scaling, and shearing) page elements, focusing especially
on the underlying *affine transform* and its operations.

For more about how to use affine transforms to achieve specific results,
see the [Size and Position Shapes](https://developers.google.com/workspace/slides/how-tos/transform) guide.

The visual size and position of a page element are controlled by two
properties: *size* and *transform* . The size describes the ideal or built-in size
of the page element being created. The transform specifies a two-dimensional
[affine transform](https://en.wikipedia.org/wiki/Affine_transformation) matrix
that specifies how an object at its built-in size is transformed to result in its
final visual appearance.

![Diagram of a shape rendered through an affine transform](https://developers.google.com/static/workspace/slides/api/images/affine.png)

When you select a page element in the Slides UI and change its visual size
using the adjustment handles, you are actually updating this transform matrix.
Moving the element across the page or rotating it also updates the element's
transform matrix.

## Use the Slides UI to get started

The matrix arithmetic that you use to transform and resize page elements is
very powerful, but can be daunting at first; most of this page describes these
calculations. However, you can simplify the specification of transforms and
sizes using the following approach:

1. Create page elements using the Slides UI.
2. Position scale these page elements as desired, still using the Slides UI.
3. Read the size and transform of those elements using the get method.

That can be enough to get you started; the rest of this guide explains the
transform calculations that you can use to manipulate page elements in detail.

## Affine transform matrices

Two-dimensional affine transform matrices are commonly used by graphics
libraries to control the scale, rotation, shear, reflection and translation of
elements. In the Slides API, a page element's transform is represented
as a 3x3 matrix:
$$A=\\begin{bmatrix} scale\\_x \& shear\\_x \& translate\\_x\\\\ shear\\_y \& scale\\_y \& translate\\_y\\\\ 0 \& 0 \& 1 \\end{bmatrix}$$

The parameters used in the transform are:

|---|---|
| `translate_x` | The translate parameters specify the (X,Y) position of the upper-left corner of the page element, relative to the upper-left corner of the page. You specify the relative position using [Units](https://developers.google.com/workspace/slides/reference/rest/v1/Unit) of either points (pt) or English metric units (EMU). |
| `translate_y` | The translate parameters specify the (X,Y) position of the upper-left corner of the page element, relative to the upper-left corner of the page. You specify the relative position using [Units](https://developers.google.com/workspace/slides/reference/rest/v1/Unit) of either points (pt) or English metric units (EMU). |
| `scale_x` | The scale parameters control how large a page element is when rendered. These are unitless multiplying factors; for example, a `scale_x` value of 1.5 enlarges the element's width by 50%. |
| `scale_y` | The scale parameters control how large a page element is when rendered. These are unitless multiplying factors; for example, a `scale_x` value of 1.5 enlarges the element's width by 50%. |
| `shear_x` | The shear parameters are also unitless and control the tilt of a page element. Scale and shear parameters can be used together to rotate a page element. |
| `shear_y` | The shear parameters are also unitless and control the tilt of a page element. Scale and shear parameters can be used together to rotate a page element. |

You can find many examples on the web showing
[how 2-D transformation matrices](https://en.wikipedia.org/wiki/Transformation_matrix#/media/File:2D_affine_transformation_matrix.svg)
affect graphical object rendering.

The transform matrix is relative to the containing group or page of the
element. For example, if you rotate a group that contains a rectangle,
the group's [`transform`](https://developers.google.com/workspace/slides/reference/rest/v1/presentations.pages#pageelement)
field values reflect the rotation, but the rectangle's [`transform`](https://developers.google.com/workspace/slides/reference/rest/v1/presentations.pages#pageelement)
field values do not.

## Calculating visual size

To determine the visual (*rendered*) size of a page element, you must consider
both the size and transform properties together. You can't determine which of
two page elements is visually larger just by comparing their size properties:
you must map the boundaries of the element using the transform matrix and
compute a rendered size.

### Mapping a point

To map a specific point using the transform matrix, convert the point (x, y)
into a vector \[x, y, 1\] and then perform matrix multiplication. Consider the
mapping of a point *p*:

\\\[p' = Ap\\\]

This becomes:
$$\\begin{bmatrix} x'\\\\ y'\\\\ 1 \\end{bmatrix} =\\begin{bmatrix} scale\\_x \& shear\\_x \& translate\\_x\\\\ shear\\_y \& scale\\_y \& translate\\_y\\\\ 0 \& 0 \& 1 \\end{bmatrix} \\begin{bmatrix} x\\\\ y\\\\ 1 \\end{bmatrix}$$

Therefore the coordinates of the new point *p'* are:
$$x' = (scale\\_x \\times x) + (shear\\_x \\times y) + translate\\_x\\\\ y' = (scale\\_y \\times y) + (shear\\_y \\times x) + translate\\_y$$

### Calculating the boundary

To determine the rendered size of an element's bounding box after a transform
that shears and scales, use:
$$width' = (scale\\_x \\times width) + (shear\\_x \\times height)\\\\ height' = (scale\\_y \\times height) + (shear\\_y \\times width)$$

For more about how to use affine transforms to achieve specific results,
see the [Size and Position Shapes](https://developers.google.com/workspace/slides/how-tos/transform) guide.

## Limitations

Some sizing and positioning fields are incompatible with some types of page
elements. The table below summarizes the compatibility of certain page elements with sizing and positioning fields.

| Field | Shape | Video | Table |
|---|---|---|---|
| **Translation** | ✔ | ✔ | ✔ |
| **Scale** | ✔ | ✔ | No\*\* |
| **Shear** | ✔ | No | No |

\*\* To update table row and column dimensions, use
`https://developers.google.com/workspace/slides/api/reference/rest/v1/presentations/request#UpdateTableRowPropertiesRequest`
and
`https://developers.google.com/workspace/slides/api/reference/rest/v1/presentations/request#UpdateTableColumnPropertiesRequest`.

All sizing and positioning fields might give unexpected results if the page element has shearing. All limitations are subject to change. For up to date information, see [Google Slides API](https://developers.google.com/workspace/slides/api/reference/rest).


# REST Resource: presentations

- [Resource: Presentation](https://developers.google.com/workspace/slides/api/reference/rest/v1/presentations#Presentation)
  - [JSON representation](https://developers.google.com/workspace/slides/api/reference/rest/v1/presentations#Presentation.SCHEMA_REPRESENTATION)
- [Methods](https://developers.google.com/workspace/slides/api/reference/rest/v1/presentations#METHODS_SUMMARY)

## Resource: Presentation

A Google Slides presentation.

| JSON representation |
|---|
| ``` { "presentationId": string, "pageSize": { object (`https://developers.google.com/workspace/slides/api/reference/rest/v1/Size`) }, "slides": [ { object (`https://developers.google.com/workspace/slides/api/reference/rest/v1/presentations.pages#Page`) } ], "title": string, "masters": [ { object (`https://developers.google.com/workspace/slides/api/reference/rest/v1/presentations.pages#Page`) } ], "layouts": [ { object (`https://developers.google.com/workspace/slides/api/reference/rest/v1/presentations.pages#Page`) } ], "locale": string, "revisionId": string, "notesMaster": { object (`https://developers.google.com/workspace/slides/api/reference/rest/v1/presentations.pages#Page`) } } ``` |

| Fields ||
|---|---|
| `presentationId` | `string` The ID of the presentation. |
| `pageSize` | ``object (`https://developers.google.com/workspace/slides/api/reference/rest/v1/Size`)`` The size of pages in the presentation. |
| `slides[]` | ``object (`https://developers.google.com/workspace/slides/api/reference/rest/v1/presentations.pages#Page`)`` The slides in the presentation. A slide inherits properties from a slide layout. |
| `title` | `string` The title of the presentation. |
| `masters[]` | ``object (`https://developers.google.com/workspace/slides/api/reference/rest/v1/presentations.pages#Page`)`` The slide masters in the presentation. A slide master contains all common page elements and the common properties for a set of layouts. They serve three purposes: - Placeholder shapes on a master contain the default text styles and shape properties of all placeholder shapes on pages that use that master. - The master page properties define the common page properties inherited by its layouts. - Any other shapes on the master slide appear on all slides using that master, regardless of their layout. |
| `layouts[]` | ``object (`https://developers.google.com/workspace/slides/api/reference/rest/v1/presentations.pages#Page`)`` The layouts in the presentation. A layout is a template that determines how content is arranged and styled on the slides that inherit from that layout. |
| `locale` | `string` The locale of the presentation, as an IETF BCP 47 language tag. |
| `revisionId` | `string` Output only. The revision ID of the presentation. Can be used in update requests to assert the presentation revision hasn't changed since the last read operation. Only populated if the user has edit access to the presentation. The revision ID is not a sequential number but a nebulous string. The format of the revision ID may change over time, so it should be treated opaquely. A returned revision ID is only guaranteed to be valid for 24 hours after it has been returned and cannot be shared across users. If the revision ID is unchanged between calls, then the presentation has not changed. Conversely, a changed ID (for the same presentation and user) usually means the presentation has been updated. However, a changed ID can also be due to internal factors such as ID format changes. |
| `notesMaster` | ``object (`https://developers.google.com/workspace/slides/api/reference/rest/v1/presentations.pages#Page`)`` The notes master in the presentation. It serves three purposes: - Placeholder shapes on a notes master contain the default text styles and shape properties of all placeholder shapes on `https://developers.google.com/workspace/slides/api/reference/rest/v1/presentations.pages#Page.SlideProperties.FIELDS.notes_page`. Specifically, a `SLIDE_IMAGE` placeholder shape contains the slide thumbnail, and a `BODY` placeholder shape contains the speaker notes. - The notes master page properties define the common page properties inherited by all `https://developers.google.com/workspace/slides/api/reference/rest/v1/presentations.pages#Page.SlideProperties.FIELDS.notes_page`. - Any other shapes on the notes master appear on all `https://developers.google.com/workspace/slides/api/reference/rest/v1/presentations.pages#Page.SlideProperties.FIELDS.notes_page`. The notes master is read-only. |

| ## Methods ||
|---|---|
| ### `https://developers.google.com/workspace/slides/api/reference/rest/v1/presentations/batchUpdate` | Applies one or more updates to the presentation. |
| ### `https://developers.google.com/workspace/slides/api/reference/rest/v1/presentations/create` | Creates a blank presentation using the title given in the request. |
| ### `https://developers.google.com/workspace/slides/api/reference/rest/v1/presentations/get` | Gets the latest version of the specified presentation. |


# Method: presentations.get

- [HTTP request](https://developers.google.com/workspace/slides/api/reference/rest/v1/presentations/get#body.HTTP_TEMPLATE)
- [Path parameters](https://developers.google.com/workspace/slides/api/reference/rest/v1/presentations/get#body.PATH_PARAMETERS)
- [Request body](https://developers.google.com/workspace/slides/api/reference/rest/v1/presentations/get#body.request_body)
- [Response body](https://developers.google.com/workspace/slides/api/reference/rest/v1/presentations/get#body.response_body)
- [Authorization scopes](https://developers.google.com/workspace/slides/api/reference/rest/v1/presentations/get#body.aspect)
- [Try it!](https://developers.google.com/workspace/slides/api/reference/rest/v1/presentations/get#try-it)

Gets the latest version of the specified presentation.

### HTTP request

`GET https://slides.googleapis.com/v1/presentations/{presentationId}`

The URL uses [gRPC Transcoding](https://google.aip.dev/127) syntax.

### Path parameters

| Parameters ||
|---|---|
| `presentationId` | `string` The ID of the presentation to retrieve. |

### Request body

The request body must be empty.

### Response body

If successful, the response body contains an instance of `https://developers.google.com/workspace/slides/api/reference/rest/v1/presentations#Presentation`.

### Authorization scopes

Requires one of the following OAuth scopes:

- `https://www.googleapis.com/auth/drive`
- `https://www.googleapis.com/auth/drive.file`
- `https://www.googleapis.com/auth/drive.readonly`
- `https://www.googleapis.com/auth/presentations`
- `https://www.googleapis.com/auth/presentations.readonly`

For more information, see the [Authorization guide](https://developers.google.com/workspace/guides/configure-oauth-consent).



# REST Resource: presentations.pages

- [Resource: Page](https://developers.google.com/workspace/slides/api/reference/rest/v1/presentations.pages#Page)
  - [JSON representation](https://developers.google.com/workspace/slides/api/reference/rest/v1/presentations.pages#Page.SCHEMA_REPRESENTATION)
  - [PageType](https://developers.google.com/workspace/slides/api/reference/rest/v1/presentations.pages#Page.PageType)
  - [PageElement](https://developers.google.com/workspace/slides/api/reference/rest/v1/presentations.pages#Page.PageElement)
    - [JSON representation](https://developers.google.com/workspace/slides/api/reference/rest/v1/presentations.pages#Page.PageElement.SCHEMA_REPRESENTATION)
  - [AffineTransform](https://developers.google.com/workspace/slides/api/reference/rest/v1/presentations.pages#Page.AffineTransform)
    - [JSON representation](https://developers.google.com/workspace/slides/api/reference/rest/v1/presentations.pages#Page.AffineTransform.SCHEMA_REPRESENTATION)
  - [Group](https://developers.google.com/workspace/slides/api/reference/rest/v1/presentations.pages#Page.Group)
    - [JSON representation](https://developers.google.com/workspace/slides/api/reference/rest/v1/presentations.pages#Page.Group.SCHEMA_REPRESENTATION)
  - [Shape](https://developers.google.com/workspace/slides/api/reference/rest/v1/presentations.pages#Page.Shape)
    - [JSON representation](https://developers.google.com/workspace/slides/api/reference/rest/v1/presentations.pages#Page.Shape.SCHEMA_REPRESENTATION)
  - [Type](https://developers.google.com/workspace/slides/api/reference/rest/v1/presentations.pages#Page.Type)
  - [TextContent](https://developers.google.com/workspace/slides/api/reference/rest/v1/presentations.pages#Page.TextContent)
    - [JSON representation](https://developers.google.com/workspace/slides/api/reference/rest/v1/presentations.pages#Page.TextContent.SCHEMA_REPRESENTATION)
  - [TextElement](https://developers.google.com/workspace/slides/api/reference/rest/v1/presentations.pages#Page.TextElement)
    - [JSON representation](https://developers.google.com/workspace/slides/api/reference/rest/v1/presentations.pages#Page.TextElement.SCHEMA_REPRESENTATION)
  - [ParagraphMarker](https://developers.google.com/workspace/slides/api/reference/rest/v1/presentations.pages#Page.ParagraphMarker)
    - [JSON representation](https://developers.google.com/workspace/slides/api/reference/rest/v1/presentations.pages#Page.ParagraphMarker.SCHEMA_REPRESENTATION)
  - [ParagraphStyle](https://developers.google.com/workspace/slides/api/reference/rest/v1/presentations.pages#Page.ParagraphStyle)
    - [JSON representation](https://developers.google.com/workspace/slides/api/reference/rest/v1/presentations.pages#Page.ParagraphStyle.SCHEMA_REPRESENTATION)
  - [Alignment](https://developers.google.com/workspace/slides/api/reference/rest/v1/presentations.pages#Page.Alignment)
  - [TextDirection](https://developers.google.com/workspace/slides/api/reference/rest/v1/presentations.pages#Page.TextDirection)
  - [SpacingMode](https://developers.google.com/workspace/slides/api/reference/rest/v1/presentations.pages#Page.SpacingMode)
  - [Bullet](https://developers.google.com/workspace/slides/api/reference/rest/v1/presentations.pages#Page.Bullet)
    - [JSON representation](https://developers.google.com/workspace/slides/api/reference/rest/v1/presentations.pages#Page.Bullet.SCHEMA_REPRESENTATION)
  - [TextStyle](https://developers.google.com/workspace/slides/api/reference/rest/v1/presentations.pages#Page.TextStyle)
    - [JSON representation](https://developers.google.com/workspace/slides/api/reference/rest/v1/presentations.pages#Page.TextStyle.SCHEMA_REPRESENTATION)
  - [OptionalColor](https://developers.google.com/workspace/slides/api/reference/rest/v1/presentations.pages#Page.OptionalColor)
    - [JSON representation](https://developers.google.com/workspace/slides/api/reference/rest/v1/presentations.pages#Page.OptionalColor.SCHEMA_REPRESENTATION)
  - [OpaqueColor](https://developers.google.com/workspace/slides/api/reference/rest/v1/presentations.pages#Page.OpaqueColor)
    - [JSON representation](https://developers.google.com/workspace/slides/api/reference/rest/v1/presentations.pages#Page.OpaqueColor.SCHEMA_REPRESENTATION)
  - [RgbColor](https://developers.google.com/workspace/slides/api/reference/rest/v1/presentations.pages#Page.RgbColor)
    - [JSON representation](https://developers.google.com/workspace/slides/api/reference/rest/v1/presentations.pages#Page.RgbColor.SCHEMA_REPRESENTATION)
  - [ThemeColorType](https://developers.google.com/workspace/slides/api/reference/rest/v1/presentations.pages#Page.ThemeColorType)
  - [Link](https://developers.google.com/workspace/slides/api/reference/rest/v1/presentations.pages#Page.Link)
    - [JSON representation](https://developers.google.com/workspace/slides/api/reference/rest/v1/presentations.pages#Page.Link.SCHEMA_REPRESENTATION)
  - [RelativeSlideLink](https://developers.google.com/workspace/slides/api/reference/rest/v1/presentations.pages#Page.RelativeSlideLink)
  - [BaselineOffset](https://developers.google.com/workspace/slides/api/reference/rest/v1/presentations.pages#Page.BaselineOffset)
  - [WeightedFontFamily](https://developers.google.com/workspace/slides/api/reference/rest/v1/presentations.pages#Page.WeightedFontFamily)
    - [JSON representation](https://developers.google.com/workspace/slides/api/reference/rest/v1/presentations.pages#Page.WeightedFontFamily.SCHEMA_REPRESENTATION)
  - [TextRun](https://developers.google.com/workspace/slides/api/reference/rest/v1/presentations.pages#Page.TextRun)
    - [JSON representation](https://developers.google.com/workspace/slides/api/reference/rest/v1/presentations.pages#Page.TextRun.SCHEMA_REPRESENTATION)
  - [AutoText](https://developers.google.com/workspace/slides/api/reference/rest/v1/presentations.pages#Page.AutoText)
    - [JSON representation](https://developers.google.com/workspace/slides/api/reference/rest/v1/presentations.pages#Page.AutoText.SCHEMA_REPRESENTATION)
  - [Type](https://developers.google.com/workspace/slides/api/reference/rest/v1/presentations.pages#Page.Type_1)
  - [List](https://developers.google.com/workspace/slides/api/reference/rest/v1/presentations.pages#Page.List)
    - [JSON representation](https://developers.google.com/workspace/slides/api/reference/rest/v1/presentations.pages#Page.List.SCHEMA_REPRESENTATION)
  - [NestingLevel](https://developers.google.com/workspace/slides/api/reference/rest/v1/presentations.pages#Page.NestingLevel)
    - [JSON representation](https://developers.google.com/workspace/slides/api/reference/rest/v1/presentations.pages#Page.NestingLevel.SCHEMA_REPRESENTATION)
  - [ShapeProperties](https://developers.google.com/workspace/slides/api/reference/rest/v1/presentations.pages#Page.ShapeProperties)
    - [JSON representation](https://developers.google.com/workspace/slides/api/reference/rest/v1/presentations.pages#Page.ShapeProperties.SCHEMA_REPRESENTATION)
  - [ShapeBackgroundFill](https://developers.google.com/workspace/slides/api/reference/rest/v1/presentations.pages#Page.ShapeBackgroundFill)
    - [JSON representation](https://developers.google.com/workspace/slides/api/reference/rest/v1/presentations.pages#Page.ShapeBackgroundFill.SCHEMA_REPRESENTATION)
  - [PropertyState](https://developers.google.com/workspace/slides/api/reference/rest/v1/presentations.pages#Page.PropertyState)
  - [SolidFill](https://developers.google.com/workspace/slides/api/reference/rest/v1/presentations.pages#Page.SolidFill)
    - [JSON representation](https://developers.google.com/workspace/slides/api/reference/rest/v1/presentations.pages#Page.SolidFill.SCHEMA_REPRESENTATION)
  - [Outline](https://developers.google.com/workspace/slides/api/reference/rest/v1/presentations.pages#Page.Outline)
    - [JSON representation](https://developers.google.com/workspace/slides/api/reference/rest/v1/presentations.pages#Page.Outline.SCHEMA_REPRESENTATION)
  - [OutlineFill](https://developers.google.com/workspace/slides/api/reference/rest/v1/presentations.pages#Page.OutlineFill)
    - [JSON representation](https://developers.google.com/workspace/slides/api/reference/rest/v1/presentations.pages#Page.OutlineFill.SCHEMA_REPRESENTATION)
  - [DashStyle](https://developers.google.com/workspace/slides/api/reference/rest/v1/presentations.pages#Page.DashStyle)
  - [Shadow](https://developers.google.com/workspace/slides/api/reference/rest/v1/presentations.pages#Page.Shadow)
    - [JSON representation](https://developers.google.com/workspace/slides/api/reference/rest/v1/presentations.pages#Page.Shadow.SCHEMA_REPRESENTATION)
  - [Type](https://developers.google.com/workspace/slides/api/reference/rest/v1/presentations.pages#Page.Type_2)
  - [RectanglePosition](https://developers.google.com/workspace/slides/api/reference/rest/v1/presentations.pages#Page.RectanglePosition)
  - [ContentAlignment](https://developers.google.com/workspace/slides/api/reference/rest/v1/presentations.pages#Page.ContentAlignment)
  - [Autofit](https://developers.google.com/workspace/slides/api/reference/rest/v1/presentations.pages#Page.Autofit)
    - [JSON representation](https://developers.google.com/workspace/slides/api/reference/rest/v1/presentations.pages#Page.Autofit.SCHEMA_REPRESENTATION)
  - [AutofitType](https://developers.google.com/workspace/slides/api/reference/rest/v1/presentations.pages#Page.AutofitType)
  - [Placeholder](https://developers.google.com/workspace/slides/api/reference/rest/v1/presentations.pages#Page.Placeholder)
    - [JSON representation](https://developers.google.com/workspace/slides/api/reference/rest/v1/presentations.pages#Page.Placeholder.SCHEMA_REPRESENTATION)
  - [Type](https://developers.google.com/workspace/slides/api/reference/rest/v1/presentations.pages#Page.Type_3)
  - [Image](https://developers.google.com/workspace/slides/api/reference/rest/v1/presentations.pages#Page.Image)
    - [JSON representation](https://developers.google.com/workspace/slides/api/reference/rest/v1/presentations.pages#Page.Image.SCHEMA_REPRESENTATION)
  - [ImageProperties](https://developers.google.com/workspace/slides/api/reference/rest/v1/presentations.pages#Page.ImageProperties)
    - [JSON representation](https://developers.google.com/workspace/slides/api/reference/rest/v1/presentations.pages#Page.ImageProperties.SCHEMA_REPRESENTATION)
  - [CropProperties](https://developers.google.com/workspace/slides/api/reference/rest/v1/presentations.pages#Page.CropProperties)
    - [JSON representation](https://developers.google.com/workspace/slides/api/reference/rest/v1/presentations.pages#Page.CropProperties.SCHEMA_REPRESENTATION)
  - [Recolor](https://developers.google.com/workspace/slides/api/reference/rest/v1/presentations.pages#Page.Recolor)
    - [JSON representation](https://developers.google.com/workspace/slides/api/reference/rest/v1/presentations.pages#Page.Recolor.SCHEMA_REPRESENTATION)
  - [ColorStop](https://developers.google.com/workspace/slides/api/reference/rest/v1/presentations.pages#Page.ColorStop)
    - [JSON representation](https://developers.google.com/workspace/slides/api/reference/rest/v1/presentations.pages#Page.ColorStop.SCHEMA_REPRESENTATION)
  - [Name](https://developers.google.com/workspace/slides/api/reference/rest/v1/presentations.pages#Page.Name)
  - [Video](https://developers.google.com/workspace/slides/api/reference/rest/v1/presentations.pages#Page.Video)
    - [JSON representation](https://developers.google.com/workspace/slides/api/reference/rest/v1/presentations.pages#Page.Video.SCHEMA_REPRESENTATION)
  - [Source](https://developers.google.com/workspace/slides/api/reference/rest/v1/presentations.pages#Page.Source)
  - [VideoProperties](https://developers.google.com/workspace/slides/api/reference/rest/v1/presentations.pages#Page.VideoProperties)
    - [JSON representation](https://developers.google.com/workspace/slides/api/reference/rest/v1/presentations.pages#Page.VideoProperties.SCHEMA_REPRESENTATION)
  - [Line](https://developers.google.com/workspace/slides/api/reference/rest/v1/presentations.pages#Page.Line)
    - [JSON representation](https://developers.google.com/workspace/slides/api/reference/rest/v1/presentations.pages#Page.Line.SCHEMA_REPRESENTATION)
  - [LineProperties](https://developers.google.com/workspace/slides/api/reference/rest/v1/presentations.pages#Page.LineProperties)
    - [JSON representation](https://developers.google.com/workspace/slides/api/reference/rest/v1/presentations.pages#Page.LineProperties.SCHEMA_REPRESENTATION)
  - [LineFill](https://developers.google.com/workspace/slides/api/reference/rest/v1/presentations.pages#Page.LineFill)
    - [JSON representation](https://developers.google.com/workspace/slides/api/reference/rest/v1/presentations.pages#Page.LineFill.SCHEMA_REPRESENTATION)
  - [ArrowStyle](https://developers.google.com/workspace/slides/api/reference/rest/v1/presentations.pages#Page.ArrowStyle)
  - [LineConnection](https://developers.google.com/workspace/slides/api/reference/rest/v1/presentations.pages#Page.LineConnection)
    - [JSON representation](https://developers.google.com/workspace/slides/api/reference/rest/v1/presentations.pages#Page.LineConnection.SCHEMA_REPRESENTATION)
  - [Type](https://developers.google.com/workspace/slides/api/reference/rest/v1/presentations.pages#Page.Type_4)
  - [LineCategory](https://developers.google.com/workspace/slides/api/reference/rest/v1/presentations.pages#Page.LineCategory)
  - [Table](https://developers.google.com/workspace/slides/api/reference/rest/v1/presentations.pages#Page.Table)
    - [JSON representation](https://developers.google.com/workspace/slides/api/reference/rest/v1/presentations.pages#Page.Table.SCHEMA_REPRESENTATION)
  - [TableRow](https://developers.google.com/workspace/slides/api/reference/rest/v1/presentations.pages#Page.TableRow)
    - [JSON representation](https://developers.google.com/workspace/slides/api/reference/rest/v1/presentations.pages#Page.TableRow.SCHEMA_REPRESENTATION)
  - [TableRowProperties](https://developers.google.com/workspace/slides/api/reference/rest/v1/presentations.pages#Page.TableRowProperties)
    - [JSON representation](https://developers.google.com/workspace/slides/api/reference/rest/v1/presentations.pages#Page.TableRowProperties.SCHEMA_REPRESENTATION)
  - [TableCell](https://developers.google.com/workspace/slides/api/reference/rest/v1/presentations.pages#Page.TableCell)
    - [JSON representation](https://developers.google.com/workspace/slides/api/reference/rest/v1/presentations.pages#Page.TableCell.SCHEMA_REPRESENTATION)
  - [TableCellLocation](https://developers.google.com/workspace/slides/api/reference/rest/v1/presentations.pages#Page.TableCellLocation)
    - [JSON representation](https://developers.google.com/workspace/slides/api/reference/rest/v1/presentations.pages#Page.TableCellLocation.SCHEMA_REPRESENTATION)
  - [TableCellProperties](https://developers.google.com/workspace/slides/api/reference/rest/v1/presentations.pages#Page.TableCellProperties)
    - [JSON representation](https://developers.google.com/workspace/slides/api/reference/rest/v1/presentations.pages#Page.TableCellProperties.SCHEMA_REPRESENTATION)
  - [TableCellBackgroundFill](https://developers.google.com/workspace/slides/api/reference/rest/v1/presentations.pages#Page.TableCellBackgroundFill)
    - [JSON representation](https://developers.google.com/workspace/slides/api/reference/rest/v1/presentations.pages#Page.TableCellBackgroundFill.SCHEMA_REPRESENTATION)
  - [TableColumnProperties](https://developers.google.com/workspace/slides/api/reference/rest/v1/presentations.pages#Page.TableColumnProperties)
    - [JSON representation](https://developers.google.com/workspace/slides/api/reference/rest/v1/presentations.pages#Page.TableColumnProperties.SCHEMA_REPRESENTATION)
  - [TableBorderRow](https://developers.google.com/workspace/slides/api/reference/rest/v1/presentations.pages#Page.TableBorderRow)
    - [JSON representation](https://developers.google.com/workspace/slides/api/reference/rest/v1/presentations.pages#Page.TableBorderRow.SCHEMA_REPRESENTATION)
  - [TableBorderCell](https://developers.google.com/workspace/slides/api/reference/rest/v1/presentations.pages#Page.TableBorderCell)
    - [JSON representation](https://developers.google.com/workspace/slides/api/reference/rest/v1/presentations.pages#Page.TableBorderCell.SCHEMA_REPRESENTATION)
  - [TableBorderProperties](https://developers.google.com/workspace/slides/api/reference/rest/v1/presentations.pages#Page.TableBorderProperties)
    - [JSON representation](https://developers.google.com/workspace/slides/api/reference/rest/v1/presentations.pages#Page.TableBorderProperties.SCHEMA_REPRESENTATION)
  - [TableBorderFill](https://developers.google.com/workspace/slides/api/reference/rest/v1/presentations.pages#Page.TableBorderFill)
    - [JSON representation](https://developers.google.com/workspace/slides/api/reference/rest/v1/presentations.pages#Page.TableBorderFill.SCHEMA_REPRESENTATION)
  - [WordArt](https://developers.google.com/workspace/slides/api/reference/rest/v1/presentations.pages#Page.WordArt)
    - [JSON representation](https://developers.google.com/workspace/slides/api/reference/rest/v1/presentations.pages#Page.WordArt.SCHEMA_REPRESENTATION)
  - [SheetsChart](https://developers.google.com/workspace/slides/api/reference/rest/v1/presentations.pages#Page.SheetsChart)
    - [JSON representation](https://developers.google.com/workspace/slides/api/reference/rest/v1/presentations.pages#Page.SheetsChart.SCHEMA_REPRESENTATION)
  - [SheetsChartProperties](https://developers.google.com/workspace/slides/api/reference/rest/v1/presentations.pages#Page.SheetsChartProperties)
    - [JSON representation](https://developers.google.com/workspace/slides/api/reference/rest/v1/presentations.pages#Page.SheetsChartProperties.SCHEMA_REPRESENTATION)
  - [SpeakerSpotlight](https://developers.google.com/workspace/slides/api/reference/rest/v1/presentations.pages#Page.SpeakerSpotlight)
    - [JSON representation](https://developers.google.com/workspace/slides/api/reference/rest/v1/presentations.pages#Page.SpeakerSpotlight.SCHEMA_REPRESENTATION)
  - [SpeakerSpotlightProperties](https://developers.google.com/workspace/slides/api/reference/rest/v1/presentations.pages#Page.SpeakerSpotlightProperties)
    - [JSON representation](https://developers.google.com/workspace/slides/api/reference/rest/v1/presentations.pages#Page.SpeakerSpotlightProperties.SCHEMA_REPRESENTATION)
  - [SlideProperties](https://developers.google.com/workspace/slides/api/reference/rest/v1/presentations.pages#Page.SlideProperties)
    - [JSON representation](https://developers.google.com/workspace/slides/api/reference/rest/v1/presentations.pages#Page.SlideProperties.SCHEMA_REPRESENTATION)
  - [LayoutProperties](https://developers.google.com/workspace/slides/api/reference/rest/v1/presentations.pages#Page.LayoutProperties)
    - [JSON representation](https://developers.google.com/workspace/slides/api/reference/rest/v1/presentations.pages#Page.LayoutProperties.SCHEMA_REPRESENTATION)
  - [NotesProperties](https://developers.google.com/workspace/slides/api/reference/rest/v1/presentations.pages#Page.NotesProperties)
    - [JSON representation](https://developers.google.com/workspace/slides/api/reference/rest/v1/presentations.pages#Page.NotesProperties.SCHEMA_REPRESENTATION)
  - [MasterProperties](https://developers.google.com/workspace/slides/api/reference/rest/v1/presentations.pages#Page.MasterProperties)
    - [JSON representation](https://developers.google.com/workspace/slides/api/reference/rest/v1/presentations.pages#Page.MasterProperties.SCHEMA_REPRESENTATION)
  - [PageProperties](https://developers.google.com/workspace/slides/api/reference/rest/v1/presentations.pages#Page.PageProperties)
    - [JSON representation](https://developers.google.com/workspace/slides/api/reference/rest/v1/presentations.pages#Page.PageProperties.SCHEMA_REPRESENTATION)
  - [PageBackgroundFill](https://developers.google.com/workspace/slides/api/reference/rest/v1/presentations.pages#Page.PageBackgroundFill)
    - [JSON representation](https://developers.google.com/workspace/slides/api/reference/rest/v1/presentations.pages#Page.PageBackgroundFill.SCHEMA_REPRESENTATION)
  - [StretchedPictureFill](https://developers.google.com/workspace/slides/api/reference/rest/v1/presentations.pages#Page.StretchedPictureFill)
    - [JSON representation](https://developers.google.com/workspace/slides/api/reference/rest/v1/presentations.pages#Page.StretchedPictureFill.SCHEMA_REPRESENTATION)
  - [ColorScheme](https://developers.google.com/workspace/slides/api/reference/rest/v1/presentations.pages#Page.ColorScheme)
    - [JSON representation](https://developers.google.com/workspace/slides/api/reference/rest/v1/presentations.pages#Page.ColorScheme.SCHEMA_REPRESENTATION)
  - [ThemeColorPair](https://developers.google.com/workspace/slides/api/reference/rest/v1/presentations.pages#Page.ThemeColorPair)
    - [JSON representation](https://developers.google.com/workspace/slides/api/reference/rest/v1/presentations.pages#Page.ThemeColorPair.SCHEMA_REPRESENTATION)
- [Methods](https://developers.google.com/workspace/slides/api/reference/rest/v1/presentations.pages#METHODS_SUMMARY)

## Resource: Page

A page in a presentation.

| JSON representation |
|---|
| ``` { "objectId": string, "pageType": enum (`https://developers.google.com/workspace/slides/api/reference/rest/v1/presentations.pages#Page.PageType`), "pageElements": [ { object (`https://developers.google.com/workspace/slides/api/reference/rest/v1/presentations.pages#Page.PageElement`) } ], "revisionId": string, "pageProperties": { object (`https://developers.google.com/workspace/slides/api/reference/rest/v1/presentations.pages#Page.PageProperties`) }, "slideProperties": { object (`https://developers.google.com/workspace/slides/api/reference/rest/v1/presentations.pages#Page.SlideProperties`) }, "layoutProperties": { object (`https://developers.google.com/workspace/slides/api/reference/rest/v1/presentations.pages#Page.LayoutProperties`) }, "notesProperties": { object (`https://developers.google.com/workspace/slides/api/reference/rest/v1/presentations.pages#Page.NotesProperties`) }, "masterProperties": { object (`https://developers.google.com/workspace/slides/api/reference/rest/v1/presentations.pages#Page.MasterProperties`) } } ``` |

| Fields ||
|---|---|
| `objectId` | `string` The object ID for this page. Object IDs used by `https://developers.google.com/workspace/slides/api/reference/rest/v1/presentations.pages#Page` and `https://developers.google.com/workspace/slides/api/reference/rest/v1/presentations.pages#Page.PageElement` share the same namespace. |
| `pageType` | ``enum (`https://developers.google.com/workspace/slides/api/reference/rest/v1/presentations.pages#Page.PageType`)`` The type of the page. |
| `pageElements[]` | ``object (`https://developers.google.com/workspace/slides/api/reference/rest/v1/presentations.pages#Page.PageElement`)`` The page elements rendered on the page. |
| `revisionId` | `string` Output only. The revision ID of the presentation. Can be used in update requests to assert the presentation revision hasn't changed since the last read operation. Only populated if the user has edit access to the presentation. The revision ID is not a sequential number but an opaque string. The format of the revision ID might change over time. A returned revision ID is only guaranteed to be valid for 24 hours after it has been returned and cannot be shared across users. If the revision ID is unchanged between calls, then the presentation has not changed. Conversely, a changed ID (for the same presentation and user) usually means the presentation has been updated. However, a changed ID can also be due to internal factors such as ID format changes. |
| `pageProperties` | ``object (`https://developers.google.com/workspace/slides/api/reference/rest/v1/presentations.pages#Page.PageProperties`)`` The properties of the page. |
| Union field `properties`. Properties that are specific for each page type. Masters do not require any additional properties. `properties` can be only one of the following: ||
| `slideProperties` | ``object (`https://developers.google.com/workspace/slides/api/reference/rest/v1/presentations.pages#Page.SlideProperties`)`` Slide specific properties. Only set if pageType = SLIDE. |
| `layoutProperties` | ``object (`https://developers.google.com/workspace/slides/api/reference/rest/v1/presentations.pages#Page.LayoutProperties`)`` Layout specific properties. Only set if pageType = LAYOUT. |
| `notesProperties` | ``object (`https://developers.google.com/workspace/slides/api/reference/rest/v1/presentations.pages#Page.NotesProperties`)`` Notes specific properties. Only set if pageType = NOTES. |
| `masterProperties` | ``object (`https://developers.google.com/workspace/slides/api/reference/rest/v1/presentations.pages#Page.MasterProperties`)`` Master specific properties. Only set if pageType = MASTER. |

### PageType

The type of a page.

| Enums ||
|---|---|
| `SLIDE` | A slide page. |
| `MASTER` | A master slide page. |
| `LAYOUT` | A layout page. |
| `NOTES` | A notes page. |
| `NOTES_MASTER` | A notes master page. |

### PageElement

A visual element rendered on a page.

| JSON representation |
|---|
| ``` { "objectId": string, "size": { object (`https://developers.google.com/workspace/slides/api/reference/rest/v1/Size`) }, "transform": { object (`https://developers.google.com/workspace/slides/api/reference/rest/v1/presentations.pages/other#Page.AffineTransform`) }, "title": string, "description": string, "elementGroup": { object (`https://developers.google.com/workspace/slides/api/reference/rest/v1/presentations.pages#Page.Group`) }, "shape": { object (`https://developers.google.com/workspace/slides/api/reference/rest/v1/presentations.pages/shapes#Page.Shape`) }, "image": { object (`https://developers.google.com/workspace/slides/api/reference/rest/v1/presentations.pages/images#Page.Image`) }, "video": { object (`https://developers.google.com/workspace/slides/api/reference/rest/v1/presentations.pages/videos#Page.Video`) }, "line": { object (`https://developers.google.com/workspace/slides/api/reference/rest/v1/presentations.pages/lines#Page.Line`) }, "table": { object (`https://developers.google.com/workspace/slides/api/reference/rest/v1/presentations.pages/tables#Page.Table`) }, "wordArt": { object (`https://developers.google.com/workspace/slides/api/reference/rest/v1/presentations.pages#Page.WordArt`) }, "sheetsChart": { object (`https://developers.google.com/workspace/slides/api/reference/rest/v1/presentations.pages/charts#Page.SheetsChart`) }, "speakerSpotlight": { object (`https://developers.google.com/workspace/slides/api/reference/rest/v1/presentations.pages#Page.SpeakerSpotlight`) } } ``` |

| Fields ||
|---|---|
| `objectId` | `string` The object ID for this page element. Object IDs used by `https://developers.google.com/workspace/slides/api/reference/rest/v1/presentations.pages#Page` and `https://developers.google.com/workspace/slides/api/reference/rest/v1/presentations.pages#Page.PageElement` share the same namespace. |
| `size` | ``object (`https://developers.google.com/workspace/slides/api/reference/rest/v1/Size`)`` The size of the page element. |
| `transform` | ``object (`https://developers.google.com/workspace/slides/api/reference/rest/v1/presentations.pages/other#Page.AffineTransform`)`` The transform of the page element. The visual appearance of the page element is determined by its absolute transform. To compute the absolute transform, preconcatenate a page element's transform with the transforms of all of its parent groups. If the page element is not in a group, its absolute transform is the same as the value in this field. The initial transform for the newly created `https://developers.google.com/workspace/slides/api/reference/rest/v1/presentations.pages#Page.Group` is always the identity transform. |
| `title` | `string` The title of the page element. Combined with description to display alt text. The field is not supported for `https://developers.google.com/workspace/slides/api/reference/rest/v1/presentations.pages#Page.Group` elements. |
| `description` | `string` The description of the page element. Combined with title to display alt text. The field is not supported for `https://developers.google.com/workspace/slides/api/reference/rest/v1/presentations.pages#Page.Group` elements. |
| Union field `element_kind`. The kind of element that this PageElement represents. Each message contains any properties that are specific to that kind of page element. `element_kind` can be only one of the following: ||
| `elementGroup` | ``object (`https://developers.google.com/workspace/slides/api/reference/rest/v1/presentations.pages#Page.Group`)`` A collection of page elements joined as a single unit. |
| `shape` | ``object (`https://developers.google.com/workspace/slides/api/reference/rest/v1/presentations.pages/shapes#Page.Shape`)`` A generic shape. |
| `image` | ``object (`https://developers.google.com/workspace/slides/api/reference/rest/v1/presentations.pages/images#Page.Image`)`` An image page element. |
| `video` | ``object (`https://developers.google.com/workspace/slides/api/reference/rest/v1/presentations.pages/videos#Page.Video`)`` A video page element. |
| `line` | ``object (`https://developers.google.com/workspace/slides/api/reference/rest/v1/presentations.pages/lines#Page.Line`)`` A line page element. |
| `table` | ``object (`https://developers.google.com/workspace/slides/api/reference/rest/v1/presentations.pages/tables#Page.Table`)`` A table page element. |
| `wordArt` | ``object (`https://developers.google.com/workspace/slides/api/reference/rest/v1/presentations.pages#Page.WordArt`)`` A word art page element. |
| `sheetsChart` | ``object (`https://developers.google.com/workspace/slides/api/reference/rest/v1/presentations.pages/charts#Page.SheetsChart`)`` A linked chart embedded from Google Sheets. Unlinked charts are represented as images. |
| `speakerSpotlight` | ``object (`https://developers.google.com/workspace/slides/api/reference/rest/v1/presentations.pages#Page.SpeakerSpotlight`)`` A Speaker Spotlight. |

### Group

A `https://developers.google.com/workspace/slides/api/reference/rest/v1/presentations.pages#Page.PageElement` kind representing a joined collection of PageElements.

| JSON representation |
|---|
| ``` { "children": [ { object (`https://developers.google.com/workspace/slides/api/reference/rest/v1/presentations.pages#Page.PageElement`) } ] } ``` |

| Fields ||
|---|---|
| `children[]` | ``object (`https://developers.google.com/workspace/slides/api/reference/rest/v1/presentations.pages#Page.PageElement`)`` The collection of elements in the group. The minimum size of a group is 2. |

### WordArt

A `https://developers.google.com/workspace/slides/api/reference/rest/v1/presentations.pages#Page.PageElement` kind representing word art.

| JSON representation |
|---|
| ``` { "renderedText": string } ``` |

| Fields ||
|---|---|
| `renderedText` | `string` The text rendered as word art. |

### SpeakerSpotlight

A `https://developers.google.com/workspace/slides/api/reference/rest/v1/presentations.pages#Page.PageElement` kind representing a Speaker Spotlight.

| JSON representation |
|---|
| ``` { "speakerSpotlightProperties": { object (`https://developers.google.com/workspace/slides/api/reference/rest/v1/presentations.pages#Page.SpeakerSpotlightProperties`) } } ``` |

| Fields ||
|---|---|
| `speakerSpotlightProperties` | ``object (`https://developers.google.com/workspace/slides/api/reference/rest/v1/presentations.pages#Page.SpeakerSpotlightProperties`)`` The properties of the Speaker Spotlight. |

### SpeakerSpotlightProperties

The properties of the `https://developers.google.com/workspace/slides/api/reference/rest/v1/presentations.pages#Page.SpeakerSpotlight`.

| JSON representation |
|---|
| ``` { "outline": { object (`https://developers.google.com/workspace/slides/api/reference/rest/v1/presentations.pages/other#Page.Outline`) }, "shadow": { object (`https://developers.google.com/workspace/slides/api/reference/rest/v1/presentations.pages/other#Page.Shadow`) } } ``` |

| Fields ||
|---|---|
| `outline` | ``object (`https://developers.google.com/workspace/slides/api/reference/rest/v1/presentations.pages/other#Page.Outline`)`` The outline of the Speaker Spotlight. If not set, it has no outline. |
| `shadow` | ``object (`https://developers.google.com/workspace/slides/api/reference/rest/v1/presentations.pages/other#Page.Shadow`)`` The shadow of the Speaker Spotlight. If not set, it has no shadow. |

### SlideProperties

The properties of `https://developers.google.com/workspace/slides/api/reference/rest/v1/presentations.pages#Page` that are only relevant for pages with pageType SLIDE.

| JSON representation |
|---|
| ``` { "layoutObjectId": string, "masterObjectId": string, "notesPage": { object (`https://developers.google.com/workspace/slides/api/reference/rest/v1/presentations.pages#Page`) }, "isSkipped": boolean } ``` |

| Fields ||
|---|---|
| `layoutObjectId` | `string` The object ID of the layout that this slide is based on. This property is read-only. |
| `masterObjectId` | `string` The object ID of the master that this slide is based on. This property is read-only. |
| `notesPage` | ``object (`https://developers.google.com/workspace/slides/api/reference/rest/v1/presentations.pages#Page`)`` The notes page that this slide is associated with. It defines the visual appearance of a notes page when printing or exporting slides with speaker notes. A notes page inherits properties from the `https://developers.google.com/workspace/slides/api/reference/rest/v1/presentations#Presentation.FIELDS.notes_master`. The placeholder shape with type BODY on the notes page contains the speaker notes for this slide. The ID of this shape is identified by the `https://developers.google.com/workspace/slides/api/reference/rest/v1/presentations.pages#Page.NotesProperties.FIELDS.speaker_notes_object_id` field. The notes page is read-only except for the text content and styles of the speaker notes shape. This property is read-only. |
| `isSkipped` | `boolean` Whether the slide is skipped in the presentation mode. Defaults to false. |

### LayoutProperties

The properties of `https://developers.google.com/workspace/slides/api/reference/rest/v1/presentations.pages#Page` are only relevant for pages with pageType LAYOUT.

| JSON representation |
|---|
| ``` { "masterObjectId": string, "name": string, "displayName": string } ``` |

| Fields ||
|---|---|
| `masterObjectId` | `string` The object ID of the master that this layout is based on. |
| `name` | `string` The name of the layout. |
| `displayName` | `string` The human-readable name of the layout. |

### NotesProperties

The properties of `https://developers.google.com/workspace/slides/api/reference/rest/v1/presentations.pages#Page` that are only relevant for pages with pageType NOTES.

| JSON representation |
|---|
| ``` { "speakerNotesObjectId": string } ``` |

| Fields ||
|---|---|
| `speakerNotesObjectId` | `string` The object ID of the shape on this notes page that contains the speaker notes for the corresponding slide. The actual shape may not always exist on the notes page. Inserting text using this object ID will automatically create the shape. In this case, the actual shape may have different object ID. The `presentations.get` or `pages.get` action will always return the latest object ID. |

### MasterProperties

The properties of `https://developers.google.com/workspace/slides/api/reference/rest/v1/presentations.pages#Page` that are only relevant for pages with pageType MASTER.

| JSON representation |
|---|
| ``` { "displayName": string } ``` |

| Fields ||
|---|---|
| `displayName` | `string` The human-readable name of the master. |

### PageProperties

The properties of the `https://developers.google.com/workspace/slides/api/reference/rest/v1/presentations.pages#Page`.

The page will inherit properties from the parent page. Depending on the page type the hierarchy is defined in either `https://developers.google.com/workspace/slides/api/reference/rest/v1/presentations.pages#Page.SlideProperties` or `https://developers.google.com/workspace/slides/api/reference/rest/v1/presentations.pages#Page.LayoutProperties`.

| JSON representation |
|---|
| ``` { "pageBackgroundFill": { object (`https://developers.google.com/workspace/slides/api/reference/rest/v1/presentations.pages#Page.PageBackgroundFill`) }, "colorScheme": { object (`https://developers.google.com/workspace/slides/api/reference/rest/v1/presentations.pages#Page.ColorScheme`) } } ``` |

| Fields ||
|---|---|
| `pageBackgroundFill` | ``object (`https://developers.google.com/workspace/slides/api/reference/rest/v1/presentations.pages#Page.PageBackgroundFill`)`` The background fill of the page. If unset, the background fill is inherited from a parent page if it exists. If the page has no parent, then the background fill defaults to the corresponding fill in the Slides editor. |
| `colorScheme` | ``object (`https://developers.google.com/workspace/slides/api/reference/rest/v1/presentations.pages#Page.ColorScheme`)`` The color scheme of the page. If unset, the color scheme is inherited from a parent page. If the page has no parent, the color scheme uses a default Slides color scheme, matching the defaults in the Slides editor. Only the concrete colors of the first 12 `https://developers.google.com/workspace/slides/api/reference/rest/v1/presentations.pages/other#Page.ThemeColorType`s are editable. In addition, only the color scheme on `Master` pages can be updated. To update the field, a color scheme containing mappings from all the first 12 ThemeColorTypes to their concrete colors must be provided. Colors for the remaining ThemeColorTypes will be ignored. |

### PageBackgroundFill

The page background fill.

| JSON representation |
|---|
| ``` { "propertyState": enum (`https://developers.google.com/workspace/slides/api/reference/rest/v1/presentations.pages/other#Page.PropertyState`), "solidFill": { object (`https://developers.google.com/workspace/slides/api/reference/rest/v1/presentations.pages/other#Page.SolidFill`) }, "stretchedPictureFill": { object (`https://developers.google.com/workspace/slides/api/reference/rest/v1/presentations.pages#Page.StretchedPictureFill`) } } ``` |

| Fields ||
|---|---|
| `propertyState` | ``enum (`https://developers.google.com/workspace/slides/api/reference/rest/v1/presentations.pages/other#Page.PropertyState`)`` The background fill property state. Updating the fill on a page will implicitly update this field to `RENDERED`, unless another value is specified in the same request. To have no fill on a page, set this field to `NOT_RENDERED`. In this case, any other fill fields set in the same request will be ignored. |
| Union field `kind`. The kind of background fill. `kind` can be only one of the following: ||
| `solidFill` | ``object (`https://developers.google.com/workspace/slides/api/reference/rest/v1/presentations.pages/other#Page.SolidFill`)`` Solid color fill. |
| `stretchedPictureFill` | ``object (`https://developers.google.com/workspace/slides/api/reference/rest/v1/presentations.pages#Page.StretchedPictureFill`)`` Stretched picture fill. |

### StretchedPictureFill

The stretched picture fill. The page or page element is filled entirely with the specified picture. The picture is stretched to fit its container.

| JSON representation |
|---|
| ``` { "contentUrl": string, "size": { object (`https://developers.google.com/workspace/slides/api/reference/rest/v1/Size`) } } ``` |

| Fields ||
|---|---|
| `contentUrl` | `string` Reading the contentUrl: An URL to a picture with a default lifetime of 30 minutes. This URL is tagged with the account of the requester. Anyone with the URL effectively accesses the picture as the original requester. Access to the picture may be lost if the presentation's sharing settings change. Writing the contentUrl: The picture is fetched once at insertion time and a copy is stored for display inside the presentation. Pictures must be less than 50MB in size, cannot exceed 25 megapixels, and must be in one of PNG, JPEG, or GIF format. The provided URL can be at most 2 kB in length. |
| `size` | ``object (`https://developers.google.com/workspace/slides/api/reference/rest/v1/Size`)`` The original size of the picture fill. This field is read-only. |

### ColorScheme

The palette of predefined colors for a page.

| JSON representation |
|---|
| ``` { "colors": [ { object (`https://developers.google.com/workspace/slides/api/reference/rest/v1/presentations.pages#Page.ThemeColorPair`) } ] } ``` |

| Fields ||
|---|---|
| `colors[]` | ``object (`https://developers.google.com/workspace/slides/api/reference/rest/v1/presentations.pages#Page.ThemeColorPair`)`` The ThemeColorType and corresponding concrete color pairs. |

### ThemeColorPair

A pair mapping a theme color type to the concrete color it represents.

| JSON representation |
|---|
| ``` { "type": enum (`https://developers.google.com/workspace/slides/api/reference/rest/v1/presentations.pages/other#Page.ThemeColorType`), "color": { object (`https://developers.google.com/workspace/slides/api/reference/rest/v1/presentations.pages/other#Page.RgbColor`) } } ``` |

| Fields ||
|---|---|
| `type` | ``enum (`https://developers.google.com/workspace/slides/api/reference/rest/v1/presentations.pages/other#Page.ThemeColorType`)`` The type of the theme color. |
| `color` | ``object (`https://developers.google.com/workspace/slides/api/reference/rest/v1/presentations.pages/other#Page.RgbColor`)`` The concrete color corresponding to the theme color type above. |

| ## Methods ||
|---|---|
| ### `https://developers.google.com/workspace/slides/api/reference/rest/v1/presentations.pages/get` | Gets the latest version of the specified page in the presentation. |
| ### `https://developers.google.com/workspace/slides/api/reference/rest/v1/presentations.pages/getThumbnail` | Generates a thumbnail of the latest version of the specified page in the presentation and returns a URL to the thumbnail image. |

# Shapes

### Shape


A
`https://developers.google.com/workspace/slides/api/reference/rest/v1/presentations.pages#Page.PageElement`
kind representing a generic shape that doesn't have a more specific classification. For more information, see
[Size and position page elements](https://developers.google.com/workspace/slides/api/guides/transform).

| JSON representation |
|---|
| ``` { "shapeType": enum (`https://developers.google.com/workspace/slides/api/reference/rest/v1/presentations.pages/shapes#Page.Type`), "text": { object (`https://developers.google.com/workspace/slides/api/reference/rest/v1/presentations.pages/text#Page.TextContent`) }, "shapeProperties": { object (`https://developers.google.com/workspace/slides/api/reference/rest/v1/presentations.pages/shapes#Page.ShapeProperties`) }, "placeholder": { object (`https://developers.google.com/workspace/slides/api/reference/rest/v1/presentations.pages/other#Page.Placeholder`) } } ``` |

| Fields ||
|---|---|
| `shapeType` | ``enum (`https://developers.google.com/workspace/slides/api/reference/rest/v1/presentations.pages/shapes#Page.Type`)`` The type of the shape. |
| `text` | ``object (`https://developers.google.com/workspace/slides/api/reference/rest/v1/presentations.pages/text#Page.TextContent`)`` The text content of the shape. |
| `shapeProperties` | ``object (`https://developers.google.com/workspace/slides/api/reference/rest/v1/presentations.pages/shapes#Page.ShapeProperties`)`` The properties of the shape. |
| `placeholder` | ``object (`https://developers.google.com/workspace/slides/api/reference/rest/v1/presentations.pages/other#Page.Placeholder`)`` Placeholders are page elements that inherit from corresponding placeholders on layouts and masters. If set, the shape is a placeholder shape and any inherited properties can be resolved by looking at the parent placeholder identified by the `https://developers.google.com/workspace/slides/api/reference/rest/v1/presentations.pages/other#Page.Placeholder.FIELDS.parent_object_id` field. |

### Type


The shape types.


Many of these shapes correspond to predefined shapes from the ECMA-376 standard. For more information, see the description of the "ST_ShapeType" simple type in section 20.1.10.56 of "Office Open XML File Formats - Fundamentals and Markup Language Reference", part 1 of
[ECMA-376 5th edition](https://ecma-international.org/publications-and-standards/standards/ecma-376/).

| Enums ||
|---|---|
| `TYPE_UNSPECIFIED` | Default value. This value is unused. |
| `TEXT_BOX` | Text box shape. |
| `RECTANGLE` | Rectangle shape. Corresponds to ECMA-376 ST_ShapeType 'rect' |
| `ROUND_RECTANGLE` | Round corner rectangle shape. Corresponds to ECMA-376 ST_ShapeType 'roundRect' |
| `ELLIPSE` | Ellipse shape. Corresponds to ECMA-376 ST_ShapeType 'ellipse' |
| `ARC` | Curved arc shape. Corresponds to ECMA-376 ST_ShapeType 'arc' |
| `BENT_ARROW` | Bent arrow shape. Corresponds to ECMA-376 ST_ShapeType 'bentArrow' |
| `BENT_UP_ARROW` | Bent up arrow shape. Corresponds to ECMA-376 ST_ShapeType 'bentUpArrow' |
| `BEVEL` | Bevel shape. Corresponds to ECMA-376 ST_ShapeType 'bevel' |
| `BLOCK_ARC` | Block arc shape. Corresponds to ECMA-376 ST_ShapeType 'blockArc' |
| `BRACE_PAIR` | Brace pair shape. Corresponds to ECMA-376 ST_ShapeType 'bracePair' |
| `BRACKET_PAIR` | Bracket pair shape. Corresponds to ECMA-376 ST_ShapeType 'bracketPair' |
| `CAN` | Can shape. Corresponds to ECMA-376 ST_ShapeType 'can' |
| `CHEVRON` | Chevron shape. Corresponds to ECMA-376 ST_ShapeType 'chevron' |
| `CHORD` | Chord shape. Corresponds to ECMA-376 ST_ShapeType 'chord' |
| `CLOUD` | Cloud shape. Corresponds to ECMA-376 ST_ShapeType 'cloud' |
| `CORNER` | Corner shape. Corresponds to ECMA-376 ST_ShapeType 'corner' |
| `CUBE` | Cube shape. Corresponds to ECMA-376 ST_ShapeType 'cube' |
| `CURVED_DOWN_ARROW` | Curved down arrow shape. Corresponds to ECMA-376 ST_ShapeType 'curvedDownArrow' |
| `CURVED_LEFT_ARROW` | Curved left arrow shape. Corresponds to ECMA-376 ST_ShapeType 'curvedLeftArrow' |
| `CURVED_RIGHT_ARROW` | Curved right arrow shape. Corresponds to ECMA-376 ST_ShapeType 'curvedRightArrow' |
| `CURVED_UP_ARROW` | Curved up arrow shape. Corresponds to ECMA-376 ST_ShapeType 'curvedUpArrow' |
| `DECAGON` | Decagon shape. Corresponds to ECMA-376 ST_ShapeType 'decagon' |
| `DIAGONAL_STRIPE` | Diagonal stripe shape. Corresponds to ECMA-376 ST_ShapeType 'diagStripe' |
| `DIAMOND` | Diamond shape. Corresponds to ECMA-376 ST_ShapeType 'diamond' |
| `DODECAGON` | Dodecagon shape. Corresponds to ECMA-376 ST_ShapeType 'dodecagon' |
| `DONUT` | Donut shape. Corresponds to ECMA-376 ST_ShapeType 'donut' |
| `DOUBLE_WAVE` | Double wave shape. Corresponds to ECMA-376 ST_ShapeType 'doubleWave' |
| `DOWN_ARROW` | Down arrow shape. Corresponds to ECMA-376 ST_ShapeType 'downArrow' |
| `DOWN_ARROW_CALLOUT` | Callout down arrow shape. Corresponds to ECMA-376 ST_ShapeType 'downArrowCallout' |
| `FOLDED_CORNER` | Folded corner shape. Corresponds to ECMA-376 ST_ShapeType 'foldedCorner' |
| `FRAME` | Frame shape. Corresponds to ECMA-376 ST_ShapeType 'frame' |
| `HALF_FRAME` | Half frame shape. Corresponds to ECMA-376 ST_ShapeType 'halfFrame' |
| `HEART` | Heart shape. Corresponds to ECMA-376 ST_ShapeType 'heart' |
| `HEPTAGON` | Heptagon shape. Corresponds to ECMA-376 ST_ShapeType 'heptagon' |
| `HEXAGON` | Hexagon shape. Corresponds to ECMA-376 ST_ShapeType 'hexagon' |
| `HOME_PLATE` | Home plate shape. Corresponds to ECMA-376 ST_ShapeType 'homePlate' |
| `HORIZONTAL_SCROLL` | Horizontal scroll shape. Corresponds to ECMA-376 ST_ShapeType 'horizontalScroll' |
| `IRREGULAR_SEAL_1` | Irregular seal 1 shape. Corresponds to ECMA-376 ST_ShapeType 'irregularSeal1' |
| `IRREGULAR_SEAL_2` | Irregular seal 2 shape. Corresponds to ECMA-376 ST_ShapeType 'irregularSeal2' |
| `LEFT_ARROW` | Left arrow shape. Corresponds to ECMA-376 ST_ShapeType 'leftArrow' |
| `LEFT_ARROW_CALLOUT` | Callout left arrow shape. Corresponds to ECMA-376 ST_ShapeType 'leftArrowCallout' |
| `LEFT_BRACE` | Left brace shape. Corresponds to ECMA-376 ST_ShapeType 'leftBrace' |
| `LEFT_BRACKET` | Left bracket shape. Corresponds to ECMA-376 ST_ShapeType 'leftBracket' |
| `LEFT_RIGHT_ARROW` | Left right arrow shape. Corresponds to ECMA-376 ST_ShapeType 'leftRightArrow' |
| `LEFT_RIGHT_ARROW_CALLOUT` | Callout left right arrow shape. Corresponds to ECMA-376 ST_ShapeType 'leftRightArrowCallout' |
| `LEFT_RIGHT_UP_ARROW` | Left right up arrow shape. Corresponds to ECMA-376 ST_ShapeType 'leftRightUpArrow' |
| `LEFT_UP_ARROW` | Left up arrow shape. Corresponds to ECMA-376 ST_ShapeType 'leftUpArrow' |
| `LIGHTNING_BOLT` | Lightning bolt shape. Corresponds to ECMA-376 ST_ShapeType 'lightningBolt' |
| `MATH_DIVIDE` | Divide math shape. Corresponds to ECMA-376 ST_ShapeType 'mathDivide' |
| `MATH_EQUAL` | Equal math shape. Corresponds to ECMA-376 ST_ShapeType 'mathEqual' |
| `MATH_MINUS` | Minus math shape. Corresponds to ECMA-376 ST_ShapeType 'mathMinus' |
| `MATH_MULTIPLY` | Multiply math shape. Corresponds to ECMA-376 ST_ShapeType 'mathMultiply' |
| `MATH_NOT_EQUAL` | Not equal math shape. Corresponds to ECMA-376 ST_ShapeType 'mathNotEqual' |
| `MATH_PLUS` | Plus math shape. Corresponds to ECMA-376 ST_ShapeType 'mathPlus' |
| `MOON` | Moon shape. Corresponds to ECMA-376 ST_ShapeType 'moon' |
| `NO_SMOKING` | No smoking shape. Corresponds to ECMA-376 ST_ShapeType 'noSmoking' |
| `NOTCHED_RIGHT_ARROW` | Notched right arrow shape. Corresponds to ECMA-376 ST_ShapeType 'notchedRightArrow' |
| `OCTAGON` | Octagon shape. Corresponds to ECMA-376 ST_ShapeType 'octagon' |
| `PARALLELOGRAM` | Parallelogram shape. Corresponds to ECMA-376 ST_ShapeType 'parallelogram' |
| `PENTAGON` | Pentagon shape. Corresponds to ECMA-376 ST_ShapeType 'pentagon' |
| `PIE` | Pie shape. Corresponds to ECMA-376 ST_ShapeType 'pie' |
| `PLAQUE` | Plaque shape. Corresponds to ECMA-376 ST_ShapeType 'plaque' |
| `PLUS` | Plus shape. Corresponds to ECMA-376 ST_ShapeType 'plus' |
| `QUAD_ARROW` | Quad-arrow shape. Corresponds to ECMA-376 ST_ShapeType 'quadArrow' |
| `QUAD_ARROW_CALLOUT` | Callout quad-arrow shape. Corresponds to ECMA-376 ST_ShapeType 'quadArrowCallout' |
| `RIBBON` | Ribbon shape. Corresponds to ECMA-376 ST_ShapeType 'ribbon' |
| `RIBBON_2` | Ribbon 2 shape. Corresponds to ECMA-376 ST_ShapeType 'ribbon2' |
| `RIGHT_ARROW` | Right arrow shape. Corresponds to ECMA-376 ST_ShapeType 'rightArrow' |
| `RIGHT_ARROW_CALLOUT` | Callout right arrow shape. Corresponds to ECMA-376 ST_ShapeType 'rightArrowCallout' |
| `RIGHT_BRACE` | Right brace shape. Corresponds to ECMA-376 ST_ShapeType 'rightBrace' |
| `RIGHT_BRACKET` | Right bracket shape. Corresponds to ECMA-376 ST_ShapeType 'rightBracket' |
| `ROUND_1_RECTANGLE` | One round corner rectangle shape. Corresponds to ECMA-376 ST_ShapeType 'round1Rect' |
| `ROUND_2_DIAGONAL_RECTANGLE` | Two diagonal round corner rectangle shape. Corresponds to ECMA-376 ST_ShapeType 'round2DiagRect' |
| `ROUND_2_SAME_RECTANGLE` | Two same-side round corner rectangle shape. Corresponds to ECMA-376 ST_ShapeType 'round2SameRect' |
| `RIGHT_TRIANGLE` | Right triangle shape. Corresponds to ECMA-376 ST_ShapeType 'rtTriangle' |
| `SMILEY_FACE` | Smiley face shape. Corresponds to ECMA-376 ST_ShapeType 'smileyFace' |
| `SNIP_1_RECTANGLE` | One snip corner rectangle shape. Corresponds to ECMA-376 ST_ShapeType 'snip1Rect' |
| `SNIP_2_DIAGONAL_RECTANGLE` | Two diagonal snip corner rectangle shape. Corresponds to ECMA-376 ST_ShapeType 'snip2DiagRect' |
| `SNIP_2_SAME_RECTANGLE` | Two same-side snip corner rectangle shape. Corresponds to ECMA-376 ST_ShapeType 'snip2SameRect' |
| `SNIP_ROUND_RECTANGLE` | One snip one round corner rectangle shape. Corresponds to ECMA-376 ST_ShapeType 'snipRoundRect' |
| `STAR_10` | Ten pointed star shape. Corresponds to ECMA-376 ST_ShapeType 'star10' |
| `STAR_12` | Twelve pointed star shape. Corresponds to ECMA-376 ST_ShapeType 'star12' |
| `STAR_16` | Sixteen pointed star shape. Corresponds to ECMA-376 ST_ShapeType 'star16' |
| `STAR_24` | Twenty four pointed star shape. Corresponds to ECMA-376 ST_ShapeType 'star24' |
| `STAR_32` | Thirty two pointed star shape. Corresponds to ECMA-376 ST_ShapeType 'star32' |
| `STAR_4` | Four pointed star shape. Corresponds to ECMA-376 ST_ShapeType 'star4' |
| `STAR_5` | Five pointed star shape. Corresponds to ECMA-376 ST_ShapeType 'star5' |
| `STAR_6` | Six pointed star shape. Corresponds to ECMA-376 ST_ShapeType 'star6' |
| `STAR_7` | Seven pointed star shape. Corresponds to ECMA-376 ST_ShapeType 'star7' |
| `STAR_8` | Eight pointed star shape. Corresponds to ECMA-376 ST_ShapeType 'star8' |
| `STRIPED_RIGHT_ARROW` | Striped right arrow shape. Corresponds to ECMA-376 ST_ShapeType 'stripedRightArrow' |
| `SUN` | Sun shape. Corresponds to ECMA-376 ST_ShapeType 'sun' |
| `TRAPEZOID` | Trapezoid shape. Corresponds to ECMA-376 ST_ShapeType 'trapezoid' |
| `TRIANGLE` | Triangle shape. Corresponds to ECMA-376 ST_ShapeType 'triangle' |
| `UP_ARROW` | Up arrow shape. Corresponds to ECMA-376 ST_ShapeType 'upArrow' |
| `UP_ARROW_CALLOUT` | Callout up arrow shape. Corresponds to ECMA-376 ST_ShapeType 'upArrowCallout' |
| `UP_DOWN_ARROW` | Up down arrow shape. Corresponds to ECMA-376 ST_ShapeType 'upDownArrow' |
| `UTURN_ARROW` | U-turn arrow shape. Corresponds to ECMA-376 ST_ShapeType 'uturnArrow' |
| `VERTICAL_SCROLL` | Vertical scroll shape. Corresponds to ECMA-376 ST_ShapeType 'verticalScroll' |
| `WAVE` | Wave shape. Corresponds to ECMA-376 ST_ShapeType 'wave' |
| `WEDGE_ELLIPSE_CALLOUT` | Callout wedge ellipse shape. Corresponds to ECMA-376 ST_ShapeType 'wedgeEllipseCallout' |
| `WEDGE_RECTANGLE_CALLOUT` | Callout wedge rectangle shape. Corresponds to ECMA-376 ST_ShapeType 'wedgeRectCallout' |
| `WEDGE_ROUND_RECTANGLE_CALLOUT` | Callout wedge round rectangle shape. Corresponds to ECMA-376 ST_ShapeType 'wedgeRoundRectCallout' |
| `FLOW_CHART_ALTERNATE_PROCESS` | Alternate process flow shape. Corresponds to ECMA-376 ST_ShapeType 'flowChartAlternateProcess' |
| `FLOW_CHART_COLLATE` | Collate flow shape. Corresponds to ECMA-376 ST_ShapeType 'flowChartCollate' |
| `FLOW_CHART_CONNECTOR` | Connector flow shape. Corresponds to ECMA-376 ST_ShapeType 'flowChartConnector' |
| `FLOW_CHART_DECISION` | Decision flow shape. Corresponds to ECMA-376 ST_ShapeType 'flowChartDecision' |
| `FLOW_CHART_DELAY` | Delay flow shape. Corresponds to ECMA-376 ST_ShapeType 'flowChartDelay' |
| `FLOW_CHART_DISPLAY` | Display flow shape. Corresponds to ECMA-376 ST_ShapeType 'flowChartDisplay' |
| `FLOW_CHART_DOCUMENT` | Document flow shape. Corresponds to ECMA-376 ST_ShapeType 'flowChartDocument' |
| `FLOW_CHART_EXTRACT` | Extract flow shape. Corresponds to ECMA-376 ST_ShapeType 'flowChartExtract' |
| `FLOW_CHART_INPUT_OUTPUT` | Input output flow shape. Corresponds to ECMA-376 ST_ShapeType 'flowChartInputOutput' |
| `FLOW_CHART_INTERNAL_STORAGE` | Internal storage flow shape. Corresponds to ECMA-376 ST_ShapeType 'flowChartInternalStorage' |
| `FLOW_CHART_MAGNETIC_DISK` | Magnetic disk flow shape. Corresponds to ECMA-376 ST_ShapeType 'flowChartMagneticDisk' |
| `FLOW_CHART_MAGNETIC_DRUM` | Magnetic drum flow shape. Corresponds to ECMA-376 ST_ShapeType 'flowChartMagneticDrum' |
| `FLOW_CHART_MAGNETIC_TAPE` | Magnetic tape flow shape. Corresponds to ECMA-376 ST_ShapeType 'flowChartMagneticTape' |
| `FLOW_CHART_MANUAL_INPUT` | Manual input flow shape. Corresponds to ECMA-376 ST_ShapeType 'flowChartManualInput' |
| `FLOW_CHART_MANUAL_OPERATION` | Manual operation flow shape. Corresponds to ECMA-376 ST_ShapeType 'flowChartManualOperation' |
| `FLOW_CHART_MERGE` | Merge flow shape. Corresponds to ECMA-376 ST_ShapeType 'flowChartMerge' |
| `FLOW_CHART_MULTIDOCUMENT` | Multi-document flow shape. Corresponds to ECMA-376 ST_ShapeType 'flowChartMultidocument' |
| `FLOW_CHART_OFFLINE_STORAGE` | Offline storage flow shape. Corresponds to ECMA-376 ST_ShapeType 'flowChartOfflineStorage' |
| `FLOW_CHART_OFFPAGE_CONNECTOR` | Off-page connector flow shape. Corresponds to ECMA-376 ST_ShapeType 'flowChartOffpageConnector' |
| `FLOW_CHART_ONLINE_STORAGE` | Online storage flow shape. Corresponds to ECMA-376 ST_ShapeType 'flowChartOnlineStorage' |
| `FLOW_CHART_OR` | Or flow shape. Corresponds to ECMA-376 ST_ShapeType 'flowChartOr' |
| `FLOW_CHART_PREDEFINED_PROCESS` | Predefined process flow shape. Corresponds to ECMA-376 ST_ShapeType 'flowChartPredefinedProcess' |
| `FLOW_CHART_PREPARATION` | Preparation flow shape. Corresponds to ECMA-376 ST_ShapeType 'flowChartPreparation' |
| `FLOW_CHART_PROCESS` | Process flow shape. Corresponds to ECMA-376 ST_ShapeType 'flowChartProcess' |
| `FLOW_CHART_PUNCHED_CARD` | Punched card flow shape. Corresponds to ECMA-376 ST_ShapeType 'flowChartPunchedCard' |
| `FLOW_CHART_PUNCHED_TAPE` | Punched tape flow shape. Corresponds to ECMA-376 ST_ShapeType 'flowChartPunchedTape' |
| `FLOW_CHART_SORT` | Sort flow shape. Corresponds to ECMA-376 ST_ShapeType 'flowChartSort' |
| `FLOW_CHART_SUMMING_JUNCTION` | Summing junction flow shape. Corresponds to ECMA-376 ST_ShapeType 'flowChartSummingJunction' |
| `FLOW_CHART_TERMINATOR` | Terminator flow shape. Corresponds to ECMA-376 ST_ShapeType 'flowChartTerminator' |
| `ARROW_EAST` | East arrow shape. |
| `ARROW_NORTH_EAST` | Northeast arrow shape. |
| `ARROW_NORTH` | North arrow shape. |
| `SPEECH` | Speech shape. |
| `STARBURST` | Star burst shape. |
| `TEARDROP` | Teardrop shape. Corresponds to ECMA-376 ST_ShapeType 'teardrop' |
| `ELLIPSE_RIBBON` | Ellipse ribbon shape. Corresponds to ECMA-376 ST_ShapeType 'ellipseRibbon' |
| `ELLIPSE_RIBBON_2` | Ellipse ribbon 2 shape. Corresponds to ECMA-376 ST_ShapeType 'ellipseRibbon2' |
| `CLOUD_CALLOUT` | Callout cloud shape. Corresponds to ECMA-376 ST_ShapeType 'cloudCallout' |
| `CUSTOM` | Custom shape. |

### ShapeProperties


The properties of a
`https://developers.google.com/workspace/slides/api/reference/rest/v1/presentations.pages/shapes#Page.Shape`.


If the shape is a placeholder shape as determined by the
`https://developers.google.com/workspace/slides/api/reference/rest/v1/presentations.pages/other#Page.Placeholder`
field, then these properties may be inherited from a parent placeholder shape. Determining the rendered value of the property depends on the corresponding
`https://developers.google.com/workspace/slides/api/reference/rest/v1/presentations.pages/other#Page.PropertyState`
field value.


Any text autofit settings on the shape are automatically deactivated by requests that can impact how text fits in the shape.

| JSON representation |
|---|
| ``` { "shapeBackgroundFill": { object (`https://developers.google.com/workspace/slides/api/reference/rest/v1/presentations.pages/shapes#Page.ShapeBackgroundFill`) }, "outline": { object (`https://developers.google.com/workspace/slides/api/reference/rest/v1/presentations.pages/other#Page.Outline`) }, "shadow": { object (`https://developers.google.com/workspace/slides/api/reference/rest/v1/presentations.pages/other#Page.Shadow`) }, "link": { object (`https://developers.google.com/workspace/slides/api/reference/rest/v1/presentations.pages/other#Page.Link`) }, "contentAlignment": enum (`https://developers.google.com/workspace/slides/api/reference/rest/v1/presentations.pages/other#Page.ContentAlignment`), "autofit": { object (`https://developers.google.com/workspace/slides/api/reference/rest/v1/presentations.pages/shapes#Page.Autofit`) } } ``` |

| Fields ||
|---|---|
| `shapeBackgroundFill` | ``object (`https://developers.google.com/workspace/slides/api/reference/rest/v1/presentations.pages/shapes#Page.ShapeBackgroundFill`)`` The background fill of the shape. If unset, the background fill is inherited from a parent placeholder if it exists. If the shape has no parent, then the default background fill depends on the shape type, matching the defaults for new shapes created in the Slides editor. |
| `outline` | ``object (`https://developers.google.com/workspace/slides/api/reference/rest/v1/presentations.pages/other#Page.Outline`)`` The outline of the shape. If unset, the outline is inherited from a parent placeholder if it exists. If the shape has no parent, then the default outline depends on the shape type, matching the defaults for new shapes created in the Slides editor. |
| `shadow` | ``object (`https://developers.google.com/workspace/slides/api/reference/rest/v1/presentations.pages/other#Page.Shadow`)`` The shadow properties of the shape. If unset, the shadow is inherited from a parent placeholder if it exists. If the shape has no parent, then the default shadow matches the defaults for new shapes created in the Slides editor. This property is read-only. |
| `link` | ``object (`https://developers.google.com/workspace/slides/api/reference/rest/v1/presentations.pages/other#Page.Link`)`` The hyperlink destination of the shape. If unset, there is no link. Links are not inherited from parent placeholders. |
| `contentAlignment` | ``enum (`https://developers.google.com/workspace/slides/api/reference/rest/v1/presentations.pages/other#Page.ContentAlignment`)`` The alignment of the content in the shape. If `https://developers.google.com/workspace/slides/api/reference/rest/v1/presentations.pages/other#Page.ContentAlignment`, the alignment is inherited from a parent placeholder if it exists. If the shape has no parent, the default alignment matches the alignment for new shapes created in the Slides editor. |
| `autofit` | ``object (`https://developers.google.com/workspace/slides/api/reference/rest/v1/presentations.pages/shapes#Page.Autofit`)`` The autofit properties of the shape. This property is only set for shapes that allow text. |

### ShapeBackgroundFill


The shape background fill.

| JSON representation |
|---|
| ``` { "propertyState": enum (`https://developers.google.com/workspace/slides/api/reference/rest/v1/presentations.pages/other#Page.PropertyState`), "solidFill": { object (`https://developers.google.com/workspace/slides/api/reference/rest/v1/presentations.pages/other#Page.SolidFill`) } } ``` |

| Fields ||
|---|---|
| `propertyState` | ``enum (`https://developers.google.com/workspace/slides/api/reference/rest/v1/presentations.pages/other#Page.PropertyState`)`` The background fill property state. Updating the fill on a shape will implicitly update this field to `RENDERED`, unless another value is specified in the same request. To have no fill on a shape, set this field to `NOT_RENDERED`. In this case, any other fill fields set in the same request will be ignored. |
| Union field `kind`. The kind of background fill. `kind` can be only one of the following: ||
| `solidFill` | ``object (`https://developers.google.com/workspace/slides/api/reference/rest/v1/presentations.pages/other#Page.SolidFill`)`` Solid color fill. |

### Autofit


The autofit properties of a
`https://developers.google.com/workspace/slides/api/reference/rest/v1/presentations.pages/shapes#Page.Shape`. This property is only set for shapes that allow text.

| JSON representation |
|---|
| ``` { "autofitType": enum (`https://developers.google.com/workspace/slides/api/reference/rest/v1/presentations.pages/shapes#Page.AutofitType`), "fontScale": number, "lineSpacingReduction": number } ``` |

| Fields ||
|---|---|
| `autofitType` | ``enum (`https://developers.google.com/workspace/slides/api/reference/rest/v1/presentations.pages/shapes#Page.AutofitType`)`` The autofit type of the shape. If the autofit type is `https://developers.google.com/workspace/slides/api/reference/rest/v1/presentations.pages/shapes#Page.AutofitType`, the autofit type is inherited from a parent placeholder if it exists. The field is automatically set to `https://developers.google.com/workspace/slides/api/reference/rest/v1/presentations.pages/shapes#Page.AutofitType` if a request is made that might affect text fitting within its bounding text box. In this case, the `https://developers.google.com/workspace/slides/api/reference/rest/v1/presentations.pages/shapes#Page.Autofit.FIELDS.font_scale` is applied to the `https://developers.google.com/workspace/slides/api/reference/rest/v1/presentations.pages/text#Page.TextStyle.FIELDS.font_size` and the `https://developers.google.com/workspace/slides/api/reference/rest/v1/presentations.pages/shapes#Page.Autofit.FIELDS.line_spacing_reduction` is applied to the `https://developers.google.com/workspace/slides/api/reference/rest/v1/presentations.pages/text#Page.ParagraphStyle.FIELDS.line_spacing`. Both properties are also reset to default values. |
| `fontScale` | `number` The font scale applied to the shape. For shapes with `https://developers.google.com/workspace/slides/api/reference/rest/v1/presentations.pages/shapes#Page.Autofit.FIELDS.autofit_type` `https://developers.google.com/workspace/slides/api/reference/rest/v1/presentations.pages/shapes#Page.AutofitType` or `https://developers.google.com/workspace/slides/api/reference/rest/v1/presentations.pages/shapes#Page.AutofitType`, this value is the default value of 1. For `https://developers.google.com/workspace/slides/api/reference/rest/v1/presentations.pages/shapes#Page.AutofitType`, this value multiplied by the `https://developers.google.com/workspace/slides/api/reference/rest/v1/presentations.pages/text#Page.TextStyle.FIELDS.font_size` gives the font size that's rendered in the editor. This property is read-only. |
| `lineSpacingReduction` | `number` The line spacing reduction applied to the shape. For shapes with `https://developers.google.com/workspace/slides/api/reference/rest/v1/presentations.pages/shapes#Page.Autofit.FIELDS.autofit_type` `https://developers.google.com/workspace/slides/api/reference/rest/v1/presentations.pages/shapes#Page.AutofitType` or `https://developers.google.com/workspace/slides/api/reference/rest/v1/presentations.pages/shapes#Page.AutofitType`, this value is the default value of 0. For `https://developers.google.com/workspace/slides/api/reference/rest/v1/presentations.pages/shapes#Page.AutofitType`, this value subtracted from the `https://developers.google.com/workspace/slides/api/reference/rest/v1/presentations.pages/text#Page.ParagraphStyle.FIELDS.line_spacing` gives the line spacing that's rendered in the editor. This property is read-only. |

### AutofitType


The autofit types of a
`https://developers.google.com/workspace/slides/api/reference/rest/v1/presentations.pages/shapes#Page.Shape`.


Note that while all values are valid for read requests, only
`https://developers.google.com/workspace/slides/api/reference/rest/v1/presentations.pages/shapes#Page.AutofitType`
and
`https://developers.google.com/workspace/slides/api/reference/rest/v1/presentations.pages/shapes#Page.AutofitType`
are valid for write requests.

| Enums ||
|---|---|
| `AUTOFIT_TYPE_UNSPECIFIED` | The autofit type is unspecified. |
| `NONE` | Do not autofit. |
| `TEXT_AUTOFIT` | Shrink text on overflow to fit the shape. |
| `SHAPE_AUTOFIT` | Resize the shape to fit the text. |

# Lines

### Line


A
`https://developers.google.com/workspace/slides/api/reference/rest/v1/presentations.pages#Page.PageElement`
kind representing a non-connector line, straight connector, curved connector, or bent connector.

| JSON representation |
|---|
| ``` { "lineProperties": { object (`https://developers.google.com/workspace/slides/api/reference/rest/v1/presentations.pages/lines#Page.LineProperties`) }, "lineType": enum (`https://developers.google.com/workspace/slides/api/reference/rest/v1/presentations.pages/lines#Page.Type_4`), "lineCategory": enum (`https://developers.google.com/workspace/slides/api/reference/rest/v1/presentations.pages/lines#Page.LineCategory`) } ``` |

| Fields ||
|---|---|
| `lineProperties` | ``object (`https://developers.google.com/workspace/slides/api/reference/rest/v1/presentations.pages/lines#Page.LineProperties`)`` The properties of the line. |
| `lineType` | ``enum (`https://developers.google.com/workspace/slides/api/reference/rest/v1/presentations.pages/lines#Page.Type_4`)`` The type of the line. |
| `lineCategory` | ``enum (`https://developers.google.com/workspace/slides/api/reference/rest/v1/presentations.pages/lines#Page.LineCategory`)`` The category of the line. It matches the `category` specified in `https://developers.google.com/workspace/slides/api/reference/rest/v1/presentations/request#CreateLineRequest`, and can be updated with `https://developers.google.com/workspace/slides/api/reference/rest/v1/presentations/request#UpdateLineCategoryRequest`. |

### LineProperties


The properties of the
`https://developers.google.com/workspace/slides/api/reference/rest/v1/presentations.pages/lines#Page.Line`.


When unset, these fields default to values that match the appearance of new lines created in the Slides editor.

| JSON representation |
|---|
| ``` { "lineFill": { object (`https://developers.google.com/workspace/slides/api/reference/rest/v1/presentations.pages/lines#Page.LineFill`) }, "weight": { object (`https://developers.google.com/workspace/slides/api/reference/rest/v1/Dimension`) }, "dashStyle": enum (`https://developers.google.com/workspace/slides/api/reference/rest/v1/presentations.pages/other#Page.DashStyle`), "startArrow": enum (`https://developers.google.com/workspace/slides/api/reference/rest/v1/presentations.pages/lines#Page.ArrowStyle`), "endArrow": enum (`https://developers.google.com/workspace/slides/api/reference/rest/v1/presentations.pages/lines#Page.ArrowStyle`), "link": { object (`https://developers.google.com/workspace/slides/api/reference/rest/v1/presentations.pages/other#Page.Link`) }, "startConnection": { object (`https://developers.google.com/workspace/slides/api/reference/rest/v1/presentations.pages/lines#Page.LineConnection`) }, "endConnection": { object (`https://developers.google.com/workspace/slides/api/reference/rest/v1/presentations.pages/lines#Page.LineConnection`) } } ``` |

| Fields ||
|---|---|
| `lineFill` | ``object (`https://developers.google.com/workspace/slides/api/reference/rest/v1/presentations.pages/lines#Page.LineFill`)`` The fill of the line. The default line fill matches the defaults for new lines created in the Slides editor. |
| `weight` | ``object (`https://developers.google.com/workspace/slides/api/reference/rest/v1/Dimension`)`` The thickness of the line. |
| `dashStyle` | ``enum (`https://developers.google.com/workspace/slides/api/reference/rest/v1/presentations.pages/other#Page.DashStyle`)`` The dash style of the line. |
| `startArrow` | ``enum (`https://developers.google.com/workspace/slides/api/reference/rest/v1/presentations.pages/lines#Page.ArrowStyle`)`` The style of the arrow at the beginning of the line. |
| `endArrow` | ``enum (`https://developers.google.com/workspace/slides/api/reference/rest/v1/presentations.pages/lines#Page.ArrowStyle`)`` The style of the arrow at the end of the line. |
| `link` | ``object (`https://developers.google.com/workspace/slides/api/reference/rest/v1/presentations.pages/other#Page.Link`)`` The hyperlink destination of the line. If unset, there is no link. |
| `startConnection` | ``object (`https://developers.google.com/workspace/slides/api/reference/rest/v1/presentations.pages/lines#Page.LineConnection`)`` The connection at the beginning of the line. If unset, there is no connection. Only lines with a `https://developers.google.com/workspace/slides/api/reference/rest/v1/presentations.pages/lines#Page.Type_4` indicating it is a "connector" can have a `startConnection`. |
| `endConnection` | ``object (`https://developers.google.com/workspace/slides/api/reference/rest/v1/presentations.pages/lines#Page.LineConnection`)`` The connection at the end of the line. If unset, there is no connection. Only lines with a `https://developers.google.com/workspace/slides/api/reference/rest/v1/presentations.pages/lines#Page.Type_4` indicating it is a "connector" can have an `endConnection`. |

### LineFill


The fill of the line.

| JSON representation |
|---|
| ``` { "solidFill": { object (`https://developers.google.com/workspace/slides/api/reference/rest/v1/presentations.pages/other#Page.SolidFill`) } } ``` |

| Fields ||
|---|---|
| Union field `kind`. The kind of line fill. `kind` can be only one of the following: ||
| `solidFill` | ``object (`https://developers.google.com/workspace/slides/api/reference/rest/v1/presentations.pages/other#Page.SolidFill`)`` Solid color fill. |

### ArrowStyle


The kinds of start and end forms with which linear geometry can be rendered. Some values are based on the "ST_LineEndType" simple type described in section 20.1.10.33 of "Office Open XML File Formats - Fundamentals and Markup Language Reference", part 1 of
[ECMA-376 5th edition](https://ecma-international.org/publications-and-standards/standards/ecma-376/).

| Enums ||
|---|---|
| `ARROW_STYLE_UNSPECIFIED` | An unspecified arrow style. |
| `NONE` | No arrow. |
| `STEALTH_ARROW` | Arrow with notched back. Corresponds to ECMA-376 ST_LineEndType value 'stealth'. |
| `FILL_ARROW` | Filled arrow. Corresponds to ECMA-376 ST_LineEndType value 'triangle'. |
| `FILL_CIRCLE` | Filled circle. Corresponds to ECMA-376 ST_LineEndType value 'oval'. |
| `FILL_SQUARE` | Filled square. |
| `FILL_DIAMOND` | Filled diamond. Corresponds to ECMA-376 ST_LineEndType value 'diamond'. |
| `OPEN_ARROW` | Hollow arrow. |
| `OPEN_CIRCLE` | Hollow circle. |
| `OPEN_SQUARE` | Hollow square. |
| `OPEN_DIAMOND` | Hollow diamond. |

### LineConnection


The properties for one end of a
`https://developers.google.com/workspace/slides/api/reference/rest/v1/presentations.pages/lines#Page.Line`
connection.

| JSON representation |
|---|
| ``` { "connectedObjectId": string, "connectionSiteIndex": integer } ``` |

| Fields ||
|---|---|
| `connectedObjectId` | `string` The object ID of the connected page element. Some page elements, such as `https://developers.google.com/workspace/slides/api/reference/rest/v1/presentations.pages#Page.Group`, `https://developers.google.com/workspace/slides/api/reference/rest/v1/presentations.pages/tables#Page.Table`, and `https://developers.google.com/workspace/slides/api/reference/rest/v1/presentations.pages/lines#Page.Line` do not have connection sites and therefore cannot be connected to a connector line. |
| `connectionSiteIndex` | `integer` The index of the connection site on the connected page element. In most cases, it corresponds to the predefined connection site index from the ECMA-376 standard. More information on those connection sites can be found in both the description of the "cxn" attribute in section 20.1.9.9 and "Annex H. Example Predefined DrawingML Shape and Text Geometries" of "Office Open XML File Formats - Fundamentals and Markup Language Reference", part 1 of [ECMA-376 5th edition](https://ecma-international.org/publications-and-standards/standards/ecma-376/). The position of each connection site can also be viewed from Slides editor. |

### Type


The line types.


Many of these shapes correspond to predefined shapes from the ECMA-376 standard. For more information, see the description of the "ST_ShapeType" simple type in section 20.1.10.56 of "Office Open XML File Formats - Fundamentals and Markup Language Reference", part 1 of
[ECMA-376 5th edition](https://ecma-international.org/publications-and-standards/standards/ecma-376/).

| Enums ||
|---|---|
| `TYPE_UNSPECIFIED` | An unspecified line type. |
| `STRAIGHT_CONNECTOR_1` | Straight connector 1 form. Corresponds to ECMA-376 ST_ShapeType 'straightConnector1'. |
| `BENT_CONNECTOR_2` | Bent connector 2 form. Corresponds to ECMA-376 ST_ShapeType 'bentConnector2'. |
| `BENT_CONNECTOR_3` | Bent connector 3 form. Corresponds to ECMA-376 ST_ShapeType 'bentConnector3'. |
| `BENT_CONNECTOR_4` | Bent connector 4 form. Corresponds to ECMA-376 ST_ShapeType 'bentConnector4'. |
| `BENT_CONNECTOR_5` | Bent connector 5 form. Corresponds to ECMA-376 ST_ShapeType 'bentConnector5'. |
| `CURVED_CONNECTOR_2` | Curved connector 2 form. Corresponds to ECMA-376 ST_ShapeType 'curvedConnector2'. |
| `CURVED_CONNECTOR_3` | Curved connector 3 form. Corresponds to ECMA-376 ST_ShapeType 'curvedConnector3'. |
| `CURVED_CONNECTOR_4` | Curved connector 4 form. Corresponds to ECMA-376 ST_ShapeType 'curvedConnector4'. |
| `CURVED_CONNECTOR_5` | Curved connector 5 form. Corresponds to ECMA-376 ST_ShapeType 'curvedConnector5'. |
| `STRAIGHT_LINE` | Straight line. Corresponds to ECMA-376 ST_ShapeType 'line'. This line type is not a connector. |

### LineCategory


The category of a
`https://developers.google.com/workspace/slides/api/reference/rest/v1/presentations.pages/lines#Page.Line`.

| Enums ||
|---|---|
| `LINE_CATEGORY_UNSPECIFIED` | Unspecified line category. |
| `STRAIGHT` | Straight connectors, including straight connector 1. |
| `BENT` | Bent connectors, including bent connector 2 to 5. |
| `CURVED` | Curved connectors, including curved connector 2 to 5. |

# Images

### Image


A
`https://developers.google.com/workspace/slides/api/reference/rest/v1/presentations.pages#Page.PageElement`
kind representing an image.

| JSON representation |
|---|
| ``` { "contentUrl": string, "imageProperties": { object (`https://developers.google.com/workspace/slides/api/reference/rest/v1/presentations.pages/other#Page.ImageProperties`) }, "sourceUrl": string, "placeholder": { object (`https://developers.google.com/workspace/slides/api/reference/rest/v1/presentations.pages/other#Page.Placeholder`) } } ``` |

| Fields ||
|---|---|
| `contentUrl` | `string` An URL to an image with a default lifetime of 30 minutes. This URL is tagged with the account of the requester. Anyone with the URL effectively accesses the image as the original requester. Access to the image may be lost if the presentation's sharing settings change. |
| `imageProperties` | ``object (`https://developers.google.com/workspace/slides/api/reference/rest/v1/presentations.pages/other#Page.ImageProperties`)`` The properties of the image. |
| `sourceUrl` | `string` The source URL is the URL used to insert the image. The source URL can be empty. |
| `placeholder` | ``object (`https://developers.google.com/workspace/slides/api/reference/rest/v1/presentations.pages/other#Page.Placeholder`)`` Placeholders are page elements that inherit from corresponding placeholders on layouts and masters. If set, the image is a placeholder image and any inherited properties can be resolved by looking at the parent placeholder identified by the `https://developers.google.com/workspace/slides/api/reference/rest/v1/presentations.pages/other#Page.Placeholder.FIELDS.parent_object_id` field. |


# Videos

### Video


A
`https://developers.google.com/workspace/slides/api/reference/rest/v1/presentations.pages#Page.PageElement`
kind representing a video.

| JSON representation |
|---|
| ``` { "url": string, "source": enum (`https://developers.google.com/workspace/slides/api/reference/rest/v1/presentations.pages/videos#Page.Source`), "id": string, "videoProperties": { object (`https://developers.google.com/workspace/slides/api/reference/rest/v1/presentations.pages/videos#Page.VideoProperties`) } } ``` |

| Fields ||
|---|---|
| `url` | `string` An URL to a video. The URL is valid as long as the source video exists and sharing settings do not change. |
| `source` | ``enum (`https://developers.google.com/workspace/slides/api/reference/rest/v1/presentations.pages/videos#Page.Source`)`` The video source. |
| `id` | `string` The video source's unique identifier for this video. |
| `videoProperties` | ``object (`https://developers.google.com/workspace/slides/api/reference/rest/v1/presentations.pages/videos#Page.VideoProperties`)`` The properties of the video. |

### Source


The video sources.

| Enums ||
|---|---|
| `SOURCE_UNSPECIFIED` | The video source is unspecified. |
| `YOUTUBE` | The video source is YouTube. |
| `DRIVE` | The video source is Google Drive. |

### VideoProperties


The properties of the
`https://developers.google.com/workspace/slides/api/reference/rest/v1/presentations.pages/videos#Page.Video`.

| JSON representation |
|---|
| ``` { "outline": { object (`https://developers.google.com/workspace/slides/api/reference/rest/v1/presentations.pages/other#Page.Outline`) }, "autoPlay": boolean, "start": integer, "end": integer, "mute": boolean } ``` |

| Fields ||
|---|---|
| `outline` | ``object (`https://developers.google.com/workspace/slides/api/reference/rest/v1/presentations.pages/other#Page.Outline`)`` The outline of the video. The default outline matches the defaults for new videos created in the Slides editor. |
| `autoPlay` | `boolean` Whether to enable video autoplay when the page is displayed in present mode. Defaults to false. |
| `start` | `integer` The time at which to start playback, measured in seconds from the beginning of the video. If set, the start time should be before the end time. If you set this to a value that exceeds the video's length in seconds, the video will be played from the last second. If not set, the video will be played from the beginning. |
| `end` | `integer` The time at which to end playback, measured in seconds from the beginning of the video. If set, the end time should be after the start time. If not set or if you set this to a value that exceeds the video's length, the video will be played until its end. |
| `mute` | `boolean` Whether to mute the audio during video playback. Defaults to false. |


# Tables

### Table


A
`https://developers.google.com/workspace/slides/api/reference/rest/v1/presentations.pages#Page.PageElement`
kind representing a table.

| JSON representation |
|---|
| ``` { "rows": integer, "columns": integer, "tableRows": [ { object (`https://developers.google.com/workspace/slides/api/reference/rest/v1/presentations.pages/tables#Page.TableRow`) } ], "tableColumns": [ { object (`https://developers.google.com/workspace/slides/api/reference/rest/v1/presentations.pages/tables#Page.TableColumnProperties`) } ], "horizontalBorderRows": [ { object (`https://developers.google.com/workspace/slides/api/reference/rest/v1/presentations.pages/tables#Page.TableBorderRow`) } ], "verticalBorderRows": [ { object (`https://developers.google.com/workspace/slides/api/reference/rest/v1/presentations.pages/tables#Page.TableBorderRow`) } ] } ``` |

| Fields ||
|---|---|
| `rows` | `integer` Number of rows in the table. |
| `columns` | `integer` Number of columns in the table. |
| `tableRows[]` | ``object (`https://developers.google.com/workspace/slides/api/reference/rest/v1/presentations.pages/tables#Page.TableRow`)`` Properties and contents of each row. Cells that span multiple rows are contained in only one of these rows and have a `https://developers.google.com/workspace/slides/api/reference/rest/v1/presentations.pages/tables#Page.TableCell.FIELDS.row_span` greater than 1. |
| `tableColumns[]` | ``object (`https://developers.google.com/workspace/slides/api/reference/rest/v1/presentations.pages/tables#Page.TableColumnProperties`)`` Properties of each column. |
| `horizontalBorderRows[]` | ``object (`https://developers.google.com/workspace/slides/api/reference/rest/v1/presentations.pages/tables#Page.TableBorderRow`)`` Properties of horizontal cell borders. A table's horizontal cell borders are represented as a grid. The grid has one more row than the number of rows in the table and the same number of columns as the table. For example, if the table is 3 x 3, its horizontal borders will be represented as a grid with 4 rows and 3 columns. |
| `verticalBorderRows[]` | ``object (`https://developers.google.com/workspace/slides/api/reference/rest/v1/presentations.pages/tables#Page.TableBorderRow`)`` Properties of vertical cell borders. A table's vertical cell borders are represented as a grid. The grid has the same number of rows as the table and one more column than the number of columns in the table. For example, if the table is 3 x 3, its vertical borders will be represented as a grid with 3 rows and 4 columns. |

### TableRow


Properties and contents of each row in a table.

| JSON representation |
|---|
| ``` { "rowHeight": { object (`https://developers.google.com/workspace/slides/api/reference/rest/v1/Dimension`) }, "tableRowProperties": { object (`https://developers.google.com/workspace/slides/api/reference/rest/v1/presentations.pages/tables#Page.TableRowProperties`) }, "tableCells": [ { object (`https://developers.google.com/workspace/slides/api/reference/rest/v1/presentations.pages/tables#Page.TableCell`) } ] } ``` |

| Fields ||
|---|---|
| `rowHeight` | ``object (`https://developers.google.com/workspace/slides/api/reference/rest/v1/Dimension`)`` Height of a row. |
| `tableRowProperties` | ``object (`https://developers.google.com/workspace/slides/api/reference/rest/v1/presentations.pages/tables#Page.TableRowProperties`)`` Properties of the row. |
| `tableCells[]` | ``object (`https://developers.google.com/workspace/slides/api/reference/rest/v1/presentations.pages/tables#Page.TableCell`)`` Properties and contents of each cell. Cells that span multiple columns are represented only once with a `https://developers.google.com/workspace/slides/api/reference/rest/v1/presentations.pages/tables#Page.TableCell.FIELDS.column_span` greater than 1. As a result, the length of this collection does not always match the number of columns of the entire table. |

### TableRowProperties


Properties of each row in a table.

| JSON representation |
|---|
| ``` { "minRowHeight": { object (`https://developers.google.com/workspace/slides/api/reference/rest/v1/Dimension`) } } ``` |

| Fields ||
|---|---|
| `minRowHeight` | ``object (`https://developers.google.com/workspace/slides/api/reference/rest/v1/Dimension`)`` Minimum height of the row. The row will be rendered in the Slides editor at a height equal to or greater than this value in order to show all the text in the row's cell(s). |

### TableCell


Properties and contents of each table cell.

| JSON representation |
|---|
| ``` { "location": { object (`https://developers.google.com/workspace/slides/api/reference/rest/v1/presentations.pages/tables#Page.TableCellLocation`) }, "rowSpan": integer, "columnSpan": integer, "text": { object (`https://developers.google.com/workspace/slides/api/reference/rest/v1/presentations.pages/text#Page.TextContent`) }, "tableCellProperties": { object (`https://developers.google.com/workspace/slides/api/reference/rest/v1/presentations.pages/tables#Page.TableCellProperties`) } } ``` |

| Fields ||
|---|---|
| `location` | ``object (`https://developers.google.com/workspace/slides/api/reference/rest/v1/presentations.pages/tables#Page.TableCellLocation`)`` The location of the cell within the table. |
| `rowSpan` | `integer` Row span of the cell. |
| `columnSpan` | `integer` Column span of the cell. |
| `text` | ``object (`https://developers.google.com/workspace/slides/api/reference/rest/v1/presentations.pages/text#Page.TextContent`)`` The text content of the cell. |
| `tableCellProperties` | ``object (`https://developers.google.com/workspace/slides/api/reference/rest/v1/presentations.pages/tables#Page.TableCellProperties`)`` The properties of the table cell. |

### TableCellLocation


A location of a single table cell within a table.

| JSON representation |
|---|
| ``` { "rowIndex": integer, "columnIndex": integer } ``` |

| Fields ||
|---|---|
| `rowIndex` | `integer` The 0-based row index. |
| `columnIndex` | `integer` The 0-based column index. |

### TableCellProperties


The properties of the
`https://developers.google.com/workspace/slides/api/reference/rest/v1/presentations.pages/tables#Page.TableCell`.

| JSON representation |
|---|
| ``` { "tableCellBackgroundFill": { object (`https://developers.google.com/workspace/slides/api/reference/rest/v1/presentations.pages/tables#Page.TableCellBackgroundFill`) }, "contentAlignment": enum (`https://developers.google.com/workspace/slides/api/reference/rest/v1/presentations.pages/other#Page.ContentAlignment`) } ``` |

| Fields ||
|---|---|
| `tableCellBackgroundFill` | ``object (`https://developers.google.com/workspace/slides/api/reference/rest/v1/presentations.pages/tables#Page.TableCellBackgroundFill`)`` The background fill of the table cell. The default fill matches the fill for newly created table cells in the Slides editor. |
| `contentAlignment` | ``enum (`https://developers.google.com/workspace/slides/api/reference/rest/v1/presentations.pages/other#Page.ContentAlignment`)`` The alignment of the content in the table cell. The default alignment matches the alignment for newly created table cells in the Slides editor. |

### TableCellBackgroundFill


The table cell background fill.

| JSON representation |
|---|
| ``` { "propertyState": enum (`https://developers.google.com/workspace/slides/api/reference/rest/v1/presentations.pages/other#Page.PropertyState`), "solidFill": { object (`https://developers.google.com/workspace/slides/api/reference/rest/v1/presentations.pages/other#Page.SolidFill`) } } ``` |

| Fields ||
|---|---|
| `propertyState` | ``enum (`https://developers.google.com/workspace/slides/api/reference/rest/v1/presentations.pages/other#Page.PropertyState`)`` The background fill property state. Updating the fill on a table cell will implicitly update this field to `RENDERED`, unless another value is specified in the same request. To have no fill on a table cell, set this field to `NOT_RENDERED`. In this case, any other fill fields set in the same request will be ignored. |
| Union field `kind`. The kind of background fill. `kind` can be only one of the following: ||
| `solidFill` | ``object (`https://developers.google.com/workspace/slides/api/reference/rest/v1/presentations.pages/other#Page.SolidFill`)`` Solid color fill. |

### TableColumnProperties


Properties of each column in a table.

| JSON representation |
|---|
| ``` { "columnWidth": { object (`https://developers.google.com/workspace/slides/api/reference/rest/v1/Dimension`) } } ``` |

| Fields ||
|---|---|
| `columnWidth` | ``object (`https://developers.google.com/workspace/slides/api/reference/rest/v1/Dimension`)`` Width of a column. |

### TableBorderRow


Contents of each border row in a table.

| JSON representation |
|---|
| ``` { "tableBorderCells": [ { object (`https://developers.google.com/workspace/slides/api/reference/rest/v1/presentations.pages/tables#Page.TableBorderCell`) } ] } ``` |

| Fields ||
|---|---|
| `tableBorderCells[]` | ``object (`https://developers.google.com/workspace/slides/api/reference/rest/v1/presentations.pages/tables#Page.TableBorderCell`)`` Properties of each border cell. When a border's adjacent table cells are merged, it is not included in the response. |

### TableBorderCell


The properties of each border cell.

| JSON representation |
|---|
| ``` { "location": { object (`https://developers.google.com/workspace/slides/api/reference/rest/v1/presentations.pages/tables#Page.TableCellLocation`) }, "tableBorderProperties": { object (`https://developers.google.com/workspace/slides/api/reference/rest/v1/presentations.pages/tables#Page.TableBorderProperties`) } } ``` |

| Fields ||
|---|---|
| `location` | ``object (`https://developers.google.com/workspace/slides/api/reference/rest/v1/presentations.pages/tables#Page.TableCellLocation`)`` The location of the border within the border table. |
| `tableBorderProperties` | ``object (`https://developers.google.com/workspace/slides/api/reference/rest/v1/presentations.pages/tables#Page.TableBorderProperties`)`` The border properties. |

### TableBorderProperties


The border styling properties of the
`https://developers.google.com/workspace/slides/api/reference/rest/v1/presentations.pages/tables#Page.TableBorderCell`.

| JSON representation |
|---|
| ``` { "tableBorderFill": { object (`https://developers.google.com/workspace/slides/api/reference/rest/v1/presentations.pages/tables#Page.TableBorderFill`) }, "weight": { object (`https://developers.google.com/workspace/slides/api/reference/rest/v1/Dimension`) }, "dashStyle": enum (`https://developers.google.com/workspace/slides/api/reference/rest/v1/presentations.pages/other#Page.DashStyle`) } ``` |

| Fields ||
|---|---|
| `tableBorderFill` | ``object (`https://developers.google.com/workspace/slides/api/reference/rest/v1/presentations.pages/tables#Page.TableBorderFill`)`` The fill of the table border. |
| `weight` | ``object (`https://developers.google.com/workspace/slides/api/reference/rest/v1/Dimension`)`` The thickness of the border. |
| `dashStyle` | ``enum (`https://developers.google.com/workspace/slides/api/reference/rest/v1/presentations.pages/other#Page.DashStyle`)`` The dash style of the border. |

### TableBorderFill


The fill of the border.

| JSON representation |
|---|
| ``` { "solidFill": { object (`https://developers.google.com/workspace/slides/api/reference/rest/v1/presentations.pages/other#Page.SolidFill`) } } ``` |

| Fields ||
|---|---|
| Union field `kind`. The kind of fill. `kind` can be only one of the following: ||
| `solidFill` | ``object (`https://developers.google.com/workspace/slides/api/reference/rest/v1/presentations.pages/other#Page.SolidFill`)`` Solid fill. |



# Charts

### SheetsChart


A
`https://developers.google.com/workspace/slides/api/reference/rest/v1/presentations.pages#Page.PageElement`
kind representing a linked chart embedded from Google Sheets.

| JSON representation |
|---|
| ``` { "spreadsheetId": string, "chartId": integer, "contentUrl": string, "sheetsChartProperties": { object (`https://developers.google.com/workspace/slides/api/reference/rest/v1/presentations.pages/charts#Page.SheetsChartProperties`) } } ``` |

| Fields ||
|---|---|
| `spreadsheetId` | `string` The ID of the Google Sheets spreadsheet that contains the source chart. |
| `chartId` | `integer` The ID of the specific chart in the Google Sheets spreadsheet that is embedded. |
| `contentUrl` | `string` The URL of an image of the embedded chart, with a default lifetime of 30 minutes. This URL is tagged with the account of the requester. Anyone with the URL effectively accesses the image as the original requester. Access to the image may be lost if the presentation's sharing settings change. |
| `sheetsChartProperties` | ``object (`https://developers.google.com/workspace/slides/api/reference/rest/v1/presentations.pages/charts#Page.SheetsChartProperties`)`` The properties of the Sheets chart. |

### SheetsChartProperties


The properties of the
`https://developers.google.com/workspace/slides/api/reference/rest/v1/presentations.pages/charts#Page.SheetsChart`.

| JSON representation |
|---|
| ``` { "chartImageProperties": { object (`https://developers.google.com/workspace/slides/api/reference/rest/v1/presentations.pages/other#Page.ImageProperties`) } } ``` |

| Fields ||
|---|---|
| Union field `kind`. The kind of Sheets chart properties. `kind` can be only one of the following: ||
| `chartImageProperties` | ``object (`https://developers.google.com/workspace/slides/api/reference/rest/v1/presentations.pages/other#Page.ImageProperties`)`` The properties of the embedded chart image. |


# Text

### TextContent


The general text content. The text must reside in a compatible shape (e.g. text box or rectangle) or a table cell in a page.

| JSON representation |
|---|
| ``` { "textElements": [ { object (`https://developers.google.com/workspace/slides/api/reference/rest/v1/presentations.pages/text#Page.TextElement`) } ], "lists": { string: { object (`https://developers.google.com/workspace/slides/api/reference/rest/v1/presentations.pages/text#Page.List`) }, ... } } ``` |

| Fields ||
|---|---|
| `textElements[]` | ``object (`https://developers.google.com/workspace/slides/api/reference/rest/v1/presentations.pages/text#Page.TextElement`)`` The text contents broken down into its component parts, including styling information. This property is read-only. |
| `lists` | ``map (key: string, value: object ( `https://developers.google.com/workspace/slides/api/reference/rest/v1/presentations.pages/text#Page.List`))`` The bulleted lists contained in this text, keyed by list ID. |

### TextElement


A TextElement describes the content of a range of indices in the text content of a Shape or TableCell.

| JSON representation |
|---|
| ``` { "startIndex": integer, "endIndex": integer, "paragraphMarker": { object (`https://developers.google.com/workspace/slides/api/reference/rest/v1/presentations.pages/text#Page.ParagraphMarker`) }, "textRun": { object (`https://developers.google.com/workspace/slides/api/reference/rest/v1/presentations.pages/text#Page.TextRun`) }, "autoText": { object (`https://developers.google.com/workspace/slides/api/reference/rest/v1/presentations.pages/text#Page.AutoText`) } } ``` |

| Fields ||
|---|---|
| `startIndex` | `integer` The zero-based start index of this text element, in Unicode code units. |
| `endIndex` | `integer` The zero-based end index of this text element, exclusive, in Unicode code units. |
| Union field `kind`. The kind of text that this element represents. `kind` can be only one of the following: ||
| `paragraphMarker` | ``object (`https://developers.google.com/workspace/slides/api/reference/rest/v1/presentations.pages/text#Page.ParagraphMarker`)`` A marker representing the beginning of a new paragraph. The `startIndex` and `endIndex` of this TextElement represent the range of the paragraph. Other TextElements with an index range contained inside this paragraph's range are considered to be part of this paragraph. The range of indices of two separate paragraphs will never overlap. |
| `textRun` | ``object (`https://developers.google.com/workspace/slides/api/reference/rest/v1/presentations.pages/text#Page.TextRun`)`` A TextElement representing a run of text where all of the characters in the run have the same `https://developers.google.com/workspace/slides/api/reference/rest/v1/presentations.pages/text#Page.TextStyle`. The `startIndex` and `endIndex` of TextRuns will always be fully contained in the index range of a single `paragraphMarker` TextElement. In other words, a TextRun will never span multiple paragraphs. |
| `autoText` | ``object (`https://developers.google.com/workspace/slides/api/reference/rest/v1/presentations.pages/text#Page.AutoText`)`` A TextElement representing a spot in the text that is dynamically replaced with content that can change over time. |

### ParagraphMarker


A TextElement kind that represents the beginning of a new paragraph.

| JSON representation |
|---|
| ``` { "style": { object (`https://developers.google.com/workspace/slides/api/reference/rest/v1/presentations.pages/text#Page.ParagraphStyle`) }, "bullet": { object (`https://developers.google.com/workspace/slides/api/reference/rest/v1/presentations.pages/text#Page.Bullet`) } } ``` |

| Fields ||
|---|---|
| `style` | ``object (`https://developers.google.com/workspace/slides/api/reference/rest/v1/presentations.pages/text#Page.ParagraphStyle`)`` The paragraph's style |
| `bullet` | ``object (`https://developers.google.com/workspace/slides/api/reference/rest/v1/presentations.pages/text#Page.Bullet`)`` The bullet for this paragraph. If not present, the paragraph does not belong to a list. |

### ParagraphStyle


Styles that apply to a whole paragraph.


If this text is contained in a shape with a parent
`https://developers.google.com/workspace/slides/api/reference/rest/v1/presentations.pages/other#Page.Placeholder`, then these paragraph styles may be inherited from the parent. Which paragraph styles are inherited depend on the nesting level of lists:

- A paragraph not in a list will inherit its paragraph style from the paragraph at the 0 nesting level of the list inside the parent placeholder.
- A paragraph in a list will inherit its paragraph style from the paragraph at its corresponding nesting level of the list inside the parent placeholder.


Inherited paragraph styles are represented as unset fields in this message.

| JSON representation |
|---|
| ``` { "lineSpacing": number, "alignment": enum (`https://developers.google.com/workspace/slides/api/reference/rest/v1/presentations.pages/text#Page.Alignment`), "indentStart": { object (`https://developers.google.com/workspace/slides/api/reference/rest/v1/Dimension`) }, "indentEnd": { object (`https://developers.google.com/workspace/slides/api/reference/rest/v1/Dimension`) }, "spaceAbove": { object (`https://developers.google.com/workspace/slides/api/reference/rest/v1/Dimension`) }, "spaceBelow": { object (`https://developers.google.com/workspace/slides/api/reference/rest/v1/Dimension`) }, "indentFirstLine": { object (`https://developers.google.com/workspace/slides/api/reference/rest/v1/Dimension`) }, "direction": enum (`https://developers.google.com/workspace/slides/api/reference/rest/v1/presentations.pages/text#Page.TextDirection`), "spacingMode": enum (`https://developers.google.com/workspace/slides/api/reference/rest/v1/presentations.pages/text#Page.SpacingMode`) } ``` |

| Fields ||
|---|---|
| `lineSpacing` | `number` The amount of space between lines, as a percentage of normal, where normal is represented as 100.0. If unset, the value is inherited from the parent. |
| `alignment` | ``enum (`https://developers.google.com/workspace/slides/api/reference/rest/v1/presentations.pages/text#Page.Alignment`)`` The text alignment for this paragraph. |
| `indentStart` | ``object (`https://developers.google.com/workspace/slides/api/reference/rest/v1/Dimension`)`` The amount indentation for the paragraph on the side that corresponds to the start of the text, based on the current text direction. If unset, the value is inherited from the parent. |
| `indentEnd` | ``object (`https://developers.google.com/workspace/slides/api/reference/rest/v1/Dimension`)`` The amount indentation for the paragraph on the side that corresponds to the end of the text, based on the current text direction. If unset, the value is inherited from the parent. |
| `spaceAbove` | ``object (`https://developers.google.com/workspace/slides/api/reference/rest/v1/Dimension`)`` The amount of extra space above the paragraph. If unset, the value is inherited from the parent. |
| `spaceBelow` | ``object (`https://developers.google.com/workspace/slides/api/reference/rest/v1/Dimension`)`` The amount of extra space below the paragraph. If unset, the value is inherited from the parent. |
| `indentFirstLine` | ``object (`https://developers.google.com/workspace/slides/api/reference/rest/v1/Dimension`)`` The amount of indentation for the start of the first line of the paragraph. If unset, the value is inherited from the parent. |
| `direction` | ``enum (`https://developers.google.com/workspace/slides/api/reference/rest/v1/presentations.pages/text#Page.TextDirection`)`` The text direction of this paragraph. If unset, the value defaults to `https://developers.google.com/workspace/slides/api/reference/rest/v1/presentations.pages/text#Page.TextDirection` since text direction is not inherited. |
| `spacingMode` | ``enum (`https://developers.google.com/workspace/slides/api/reference/rest/v1/presentations.pages/text#Page.SpacingMode`)`` The spacing mode for the paragraph. |

### Alignment


The types of text alignment for a paragraph.

| Enums ||
|---|---|
| `ALIGNMENT_UNSPECIFIED` | The paragraph alignment is inherited from the parent. |
| `START` | The paragraph is aligned to the start of the line. Left-aligned for LTR text, right-aligned otherwise. |
| `CENTER` | The paragraph is centered. |
| `END` | The paragraph is aligned to the end of the line. Right-aligned for LTR text, left-aligned otherwise. |
| `JUSTIFIED` | The paragraph is justified. |

### TextDirection


The directions text can flow in.

| Enums ||
|---|---|
| `TEXT_DIRECTION_UNSPECIFIED` | The text direction is inherited from the parent. |
| `LEFT_TO_RIGHT` | The text goes from left to right. |
| `RIGHT_TO_LEFT` | The text goes from right to left. |

### SpacingMode


The different modes for paragraph spacing.

| Enums ||
|---|---|
| `SPACING_MODE_UNSPECIFIED` | The spacing mode is inherited from the parent. |
| `NEVER_COLLAPSE` | Paragraph spacing is always rendered. |
| `COLLAPSE_LISTS` | Paragraph spacing is skipped between list elements. |

### Bullet


Describes the bullet of a paragraph.

| JSON representation |
|---|
| ``` { "listId": string, "nestingLevel": integer, "glyph": string, "bulletStyle": { object (`https://developers.google.com/workspace/slides/api/reference/rest/v1/presentations.pages/text#Page.TextStyle`) } } ``` |

| Fields ||
|---|---|
| `listId` | `string` The ID of the list this paragraph belongs to. |
| `nestingLevel` | `integer` The nesting level of this paragraph in the list. |
| `glyph` | `string` The rendered bullet glyph for this paragraph. |
| `bulletStyle` | ``object (`https://developers.google.com/workspace/slides/api/reference/rest/v1/presentations.pages/text#Page.TextStyle`)`` The paragraph specific text style applied to this bullet. |

### TextStyle


Represents the styling that can be applied to a TextRun.


If this text is contained in a shape with a parent
`https://developers.google.com/workspace/slides/api/reference/rest/v1/presentations.pages/other#Page.Placeholder`, then these text styles may be inherited from the parent. Which text styles are inherited depend on the nesting level of lists:

- A text run in a paragraph that is not in a list will inherit its text style from the the newline character in the paragraph at the 0 nesting level of the list inside the parent placeholder.
- A text run in a paragraph that is in a list will inherit its text style from the newline character in the paragraph at its corresponding nesting level of the list inside the parent placeholder.


Inherited text styles are represented as unset fields in this message. If text is contained in a shape without a parent placeholder, unsetting these fields will revert the style to a value matching the defaults in the Slides editor.

| JSON representation |
|---|
| ``` { "backgroundColor": { object (`https://developers.google.com/workspace/slides/api/reference/rest/v1/presentations.pages/text#Page.OptionalColor`) }, "foregroundColor": { object (`https://developers.google.com/workspace/slides/api/reference/rest/v1/presentations.pages/text#Page.OptionalColor`) }, "bold": boolean, "italic": boolean, "fontFamily": string, "fontSize": { object (`https://developers.google.com/workspace/slides/api/reference/rest/v1/Dimension`) }, "link": { object (`https://developers.google.com/workspace/slides/api/reference/rest/v1/presentations.pages/other#Page.Link`) }, "baselineOffset": enum (`https://developers.google.com/workspace/slides/api/reference/rest/v1/presentations.pages/text#Page.BaselineOffset`), "smallCaps": boolean, "strikethrough": boolean, "underline": boolean, "weightedFontFamily": { object (`https://developers.google.com/workspace/slides/api/reference/rest/v1/presentations.pages/text#Page.WeightedFontFamily`) } } ``` |

| Fields ||
|---|---|
| `backgroundColor` | ``object (`https://developers.google.com/workspace/slides/api/reference/rest/v1/presentations.pages/text#Page.OptionalColor`)`` The background color of the text. If set, the color is either opaque or transparent, depending on if the `opaqueColor` field in it is set. |
| `foregroundColor` | ``object (`https://developers.google.com/workspace/slides/api/reference/rest/v1/presentations.pages/text#Page.OptionalColor`)`` The color of the text itself. If set, the color is either opaque or transparent, depending on if the `opaqueColor` field in it is set. |
| `bold` | `boolean` Whether or not the text is rendered as bold. |
| `italic` | `boolean` Whether or not the text is italicized. |
| `fontFamily` | `string` The font family of the text. The font family can be any font from the Font menu in Slides or from [Google Fonts](https://fonts.google.com/). If the font name is unrecognized, the text is rendered in `Arial`. Some fonts can affect the weight of the text. If an update request specifies values for both `fontFamily` and `bold`, the explicitly-set `bold` value is used. |
| `fontSize` | ``object (`https://developers.google.com/workspace/slides/api/reference/rest/v1/Dimension`)`` The size of the text's font. When read, the `fontSize` will specified in points. |
| `link` | ``object (`https://developers.google.com/workspace/slides/api/reference/rest/v1/presentations.pages/other#Page.Link`)`` The hyperlink destination of the text. If unset, there is no link. Links are not inherited from parent text. Changing the link in an update request causes some other changes to the text style of the range: - When setting a link, the text foreground color will be set to `https://developers.google.com/workspace/slides/api/reference/rest/v1/presentations.pages/other#Page.ThemeColorType` and the text will be underlined. If these fields are modified in the same request, those values will be used instead of the link defaults. - Setting a link on a text range that overlaps with an existing link will also update the existing link to point to the new URL. - Links are not settable on newline characters. As a result, setting a link on a text range that crosses a paragraph boundary, such as `"ABC\n123"`, will separate the newline character(s) into their own text runs. The link will be applied separately to the runs before and after the newline. - Removing a link will update the text style of the range to match the style of the preceding text (or the default text styles if the preceding text is another link) unless different styles are being set in the same request. |
| `baselineOffset` | ``enum (`https://developers.google.com/workspace/slides/api/reference/rest/v1/presentations.pages/text#Page.BaselineOffset`)`` The text's vertical offset from its normal position. Text with `SUPERSCRIPT` or `SUBSCRIPT` baseline offsets is automatically rendered in a smaller font size, computed based on the `fontSize` field. The `fontSize` itself is not affected by changes in this field. |
| `smallCaps` | `boolean` Whether or not the text is in small capital letters. |
| `strikethrough` | `boolean` Whether or not the text is struck through. |
| `underline` | `boolean` Whether or not the text is underlined. |
| `weightedFontFamily` | ``object (`https://developers.google.com/workspace/slides/api/reference/rest/v1/presentations.pages/text#Page.WeightedFontFamily`)`` The font family and rendered weight of the text. This field is an extension of `fontFamily` meant to support explicit font weights without breaking backwards compatibility. As such, when reading the style of a range of text, the value of `weightedFontFamily#fontFamily` will always be equal to that of `fontFamily`. However, when writing, if both fields are included in the field mask (either explicitly or through the wildcard `"*"`), their values are reconciled as follows: - If `fontFamily` is set and `weightedFontFamily` is not, the value of `fontFamily` is applied with weight `400` ("normal"). - If both fields are set, the value of `fontFamily` must match that of `weightedFontFamily#fontFamily`. If so, the font family and weight of `weightedFontFamily` is applied. Otherwise, a 400 bad request error is returned. - If `weightedFontFamily` is set and `fontFamily` is not, the font family and weight of `weightedFontFamily` is applied. - If neither field is set, the font family and weight of the text inherit from the parent. Note that these properties cannot inherit separately from each other. If an update request specifies values for both `weightedFontFamily` and `bold`, the `weightedFontFamily` is applied first, then `bold`. If `weightedFontFamily#weight` is not set, it defaults to `400`. If `weightedFontFamily` is set, then `weightedFontFamily#fontFamily` must also be set with a non-empty value. Otherwise, a 400 bad request error is returned. |

### OptionalColor


A color that can either be fully opaque or fully transparent.

| JSON representation |
|---|
| ``` { "opaqueColor": { object (`https://developers.google.com/workspace/slides/api/reference/rest/v1/presentations.pages/other#Page.OpaqueColor`) } } ``` |

| Fields ||
|---|---|
| `opaqueColor` | ``object (`https://developers.google.com/workspace/slides/api/reference/rest/v1/presentations.pages/other#Page.OpaqueColor`)`` If set, this will be used as an opaque color. If unset, this represents a transparent color. |

### BaselineOffset


The ways in which text can be vertically offset from its normal position.

| Enums ||
|---|---|
| `BASELINE_OFFSET_UNSPECIFIED` | The text's baseline offset is inherited from the parent. |
| `NONE` | The text is not vertically offset. |
| `SUPERSCRIPT` | The text is vertically offset upwards (superscript). |
| `SUBSCRIPT` | The text is vertically offset downwards (subscript). |

### WeightedFontFamily


Represents a font family and weight used to style a TextRun.

| JSON representation |
|---|
| ``` { "fontFamily": string, "weight": integer } ``` |

| Fields ||
|---|---|
| `fontFamily` | `string` The font family of the text. The font family can be any font from the Font menu in Slides or from [Google Fonts](https://fonts.google.com/). If the font name is unrecognized, the text is rendered in `Arial`. |
| `weight` | `integer` The rendered weight of the text. This field can have any value that is a multiple of `100` between `100` and `900`, inclusive. This range corresponds to the numerical values described in the CSS 2.1 Specification, [section 15.6](https://www.w3.org/TR/CSS21/fonts.html#font-boldness), with non-numerical values disallowed. Weights greater than or equal to `700` are considered bold, and weights less than `700` are not bold. The default value is `400` ("normal"). |

### TextRun


A TextElement kind that represents a run of text that all has the same styling.

| JSON representation |
|---|
| ``` { "content": string, "style": { object (`https://developers.google.com/workspace/slides/api/reference/rest/v1/presentations.pages/text#Page.TextStyle`) } } ``` |

| Fields ||
|---|---|
| `content` | `string` The text of this run. |
| `style` | ``object (`https://developers.google.com/workspace/slides/api/reference/rest/v1/presentations.pages/text#Page.TextStyle`)`` The styling applied to this run. |

### AutoText


A TextElement kind that represents auto text.

| JSON representation |
|---|
| ``` { "type": enum (`https://developers.google.com/workspace/slides/api/reference/rest/v1/presentations.pages/text#Page.Type_1`), "content": string, "style": { object (`https://developers.google.com/workspace/slides/api/reference/rest/v1/presentations.pages/text#Page.TextStyle`) } } ``` |

| Fields ||
|---|---|
| `type` | ``enum (`https://developers.google.com/workspace/slides/api/reference/rest/v1/presentations.pages/text#Page.Type_1`)`` The type of this auto text. |
| `content` | `string` The rendered content of this auto text, if available. |
| `style` | ``object (`https://developers.google.com/workspace/slides/api/reference/rest/v1/presentations.pages/text#Page.TextStyle`)`` The styling applied to this auto text. |

### Type


The types of auto text

| Enums ||
|---|---|
| `TYPE_UNSPECIFIED` | An unspecified autotext type. |
| `SLIDE_NUMBER` | Type for autotext that represents the current slide number. |

### List


A List describes the look and feel of bullets belonging to paragraphs associated with a list. A paragraph that is part of a list has an implicit reference to that list's ID.

| JSON representation |
|---|
| ``` { "listId": string, "nestingLevel": { integer: { object (`https://developers.google.com/workspace/slides/api/reference/rest/v1/presentations.pages/text#Page.NestingLevel`) }, ... } } ``` |

| Fields ||
|---|---|
| `listId` | `string` The ID of the list. |
| `nestingLevel` | ``map (key: integer, value: object ( `https://developers.google.com/workspace/slides/api/reference/rest/v1/presentations.pages/text#Page.NestingLevel`))`` A map of nesting levels to the properties of bullets at the associated level. A list has at most nine levels of nesting, so the possible values for the keys of this map are 0 through 8, inclusive. |

### NestingLevel


Contains properties describing the look and feel of a list bullet at a given level of nesting.

| JSON representation |
|---|
| ``` { "bulletStyle": { object (`https://developers.google.com/workspace/slides/api/reference/rest/v1/presentations.pages/text#Page.TextStyle`) } } ``` |

| Fields ||
|---|---|
| `bulletStyle` | ``object (`https://developers.google.com/workspace/slides/api/reference/rest/v1/presentations.pages/text#Page.TextStyle`)`` The style of a bullet at this level of nesting. |


# Other

### AffineTransform


AffineTransform uses a 3x3 matrix with an implied last row of \[ 0 0 1 \] to transform source coordinates (x,y) into destination coordinates (x', y') according to:

     [ x']   [  scaleX  shearX  translateX  ] [ x ]
     [ y'] = [  shearY  scaleY  translateY  ] [ y ]
     [ 1 ]   [      0       0         1     ] [ 1 ]


After transformation,

     x' = scaleX * x + shearX * y + translateX;
     y' = scaleY * y + shearY * x + translateY;


This message is therefore composed of these six matrix elements.

| JSON representation |
|---|
| ``` { "scaleX": number, "scaleY": number, "shearX": number, "shearY": number, "translateX": number, "translateY": number, "unit": enum (`https://developers.google.com/workspace/slides/api/reference/rest/v1/Unit`) } ``` |

| Fields ||
|---|---|
| `scaleX` | `number` The X coordinate scaling element. |
| `scaleY` | `number` The Y coordinate scaling element. |
| `shearX` | `number` The X coordinate shearing element. |
| `shearY` | `number` The Y coordinate shearing element. |
| `translateX` | `number` The X coordinate translation element. |
| `translateY` | `number` The Y coordinate translation element. |
| `unit` | ``enum (`https://developers.google.com/workspace/slides/api/reference/rest/v1/Unit`)`` The units for translate elements. |

### OpaqueColor


A themeable solid color value.

| JSON representation |
|---|
| ``` { "rgbColor": { object (`https://developers.google.com/workspace/slides/api/reference/rest/v1/presentations.pages/other#Page.RgbColor`) }, "themeColor": enum (`https://developers.google.com/workspace/slides/api/reference/rest/v1/presentations.pages/other#Page.ThemeColorType`) } ``` |

| Fields ||
|---|---|
| Union field `kind`. The kind of color value. `kind` can be only one of the following: ||
| `rgbColor` | ``object (`https://developers.google.com/workspace/slides/api/reference/rest/v1/presentations.pages/other#Page.RgbColor`)`` An opaque RGB color. |
| `themeColor` | ``enum (`https://developers.google.com/workspace/slides/api/reference/rest/v1/presentations.pages/other#Page.ThemeColorType`)`` An opaque theme color. |

### RgbColor


An RGB color.

| JSON representation |
|---|
| ``` { "red": number, "green": number, "blue": number } ``` |

| Fields ||
|---|---|
| `red` | `number` The red component of the color, from 0.0 to 1.0. |
| `green` | `number` The green component of the color, from 0.0 to 1.0. |
| `blue` | `number` The blue component of the color, from 0.0 to 1.0. |

### ThemeColorType


Theme color types.


`https://developers.google.com/workspace/slides/api/reference/rest/v1/presentations.pages#Page.PageProperties`
contain a
`https://developers.google.com/workspace/slides/api/reference/rest/v1/presentations.pages#Page.ColorScheme`
that defines a mapping of these theme color types to concrete colors.

| Enums ||
|---|---|
| `THEME_COLOR_TYPE_UNSPECIFIED` | Unspecified theme color. This value should not be used. |
| `DARK1` | Represents the first dark color. |
| `LIGHT1` | Represents the first light color. |
| `DARK2` | Represents the second dark color. |
| `LIGHT2` | Represents the second light color. |
| `ACCENT1` | Represents the first accent color. |
| `ACCENT2` | Represents the second accent color. |
| `ACCENT3` | Represents the third accent color. |
| `ACCENT4` | Represents the fourth accent color. |
| `ACCENT5` | Represents the fifth accent color. |
| `ACCENT6` | Represents the sixth accent color. |
| `HYPERLINK` | Represents the color to use for hyperlinks. |
| `FOLLOWED_HYPERLINK` | Represents the color to use for visited hyperlinks. |
| `TEXT1` | Represents the first text color. |
| `BACKGROUND1` | Represents the first background color. |
| `TEXT2` | Represents the second text color. |
| `BACKGROUND2` | Represents the second background color. |

### Link


A hypertext link.

| JSON representation |
|---|
| ``` { "url": string, "relativeLink": enum (`https://developers.google.com/workspace/slides/api/reference/rest/v1/presentations.pages/other#Page.RelativeSlideLink`), "pageObjectId": string, "slideIndex": integer } ``` |

| Fields ||
|---|---|
| Union field `kind`. The kinds of links. `kind` can be only one of the following: ||
| `url` | `string` If set, indicates this is a link to the external web page at this URL. |
| `relativeLink` | ``enum (`https://developers.google.com/workspace/slides/api/reference/rest/v1/presentations.pages/other#Page.RelativeSlideLink`)`` If set, indicates this is a link to a slide in this presentation, addressed by its position. |
| `pageObjectId` | `string` If set, indicates this is a link to the specific page in this presentation with this ID. A page with this ID may not exist. |
| `slideIndex` | `integer` If set, indicates this is a link to the slide at this zero-based index in the presentation. There may not be a slide at this index. |

### RelativeSlideLink


The kinds of relative links.

| Enums ||
|---|---|
| `RELATIVE_SLIDE_LINK_UNSPECIFIED` | An unspecified relative slide link. |
| `NEXT_SLIDE` | A link to the next slide. |
| `PREVIOUS_SLIDE` | A link to the previous slide. |
| `FIRST_SLIDE` | A link to the first slide in the presentation. |
| `LAST_SLIDE` | A link to the last slide in the presentation. |

### PropertyState


The possible states of a property.

| Enums ||
|---|---|
| `RENDERED` | If a property's state is RENDERED, then the element has the corresponding property when rendered on a page. If the element is a placeholder shape as determined by the `https://developers.google.com/workspace/slides/api/reference/rest/v1/presentations.pages/shapes#Page.Shape.FIELDS.placeholder` field, and it inherits from a placeholder shape, the corresponding field may be unset, meaning that the property value is inherited from a parent placeholder. If the element does not inherit, then the field will contain the rendered value. This is the default value. |
| `NOT_RENDERED` | If a property's state is NOT_RENDERED, then the element does not have the corresponding property when rendered on a page. However, the field may still be set so it can be inherited by child shapes. To remove a property from a rendered element, set its propertyState to NOT_RENDERED. |
| `INHERIT` | If a property's state is INHERIT, then the property state uses the value of corresponding `propertyState` field on the parent shape. Elements that do not inherit will never have an INHERIT property state. |

### SolidFill


A solid color fill. The page or page element is filled entirely with the specified color value.


If any field is unset, its value may be inherited from a parent placeholder if it exists.

| JSON representation |
|---|
| ``` { "color": { object (`https://developers.google.com/workspace/slides/api/reference/rest/v1/presentations.pages/other#Page.OpaqueColor`) }, "alpha": number } ``` |

| Fields ||
|---|---|
| `color` | ``object (`https://developers.google.com/workspace/slides/api/reference/rest/v1/presentations.pages/other#Page.OpaqueColor`)`` The color value of the solid fill. |
| `alpha` | `number` The fraction of this `color` that should be applied to the pixel. That is, the final pixel color is defined by the equation: pixel color = alpha \* (color) + (1.0 - alpha) \* (background color) This means that a value of 1.0 corresponds to a solid color, whereas a value of 0.0 corresponds to a completely transparent color. |

### Outline


The outline of a
`https://developers.google.com/workspace/slides/api/reference/rest/v1/presentations.pages#Page.PageElement`.


If these fields are unset, they may be inherited from a parent placeholder if it exists. If there is no parent, the fields will default to the value used for new page elements created in the Slides editor, which may depend on the page element kind.

| JSON representation |
|---|
| ``` { "outlineFill": { object (`https://developers.google.com/workspace/slides/api/reference/rest/v1/presentations.pages/other#Page.OutlineFill`) }, "weight": { object (`https://developers.google.com/workspace/slides/api/reference/rest/v1/Dimension`) }, "dashStyle": enum (`https://developers.google.com/workspace/slides/api/reference/rest/v1/presentations.pages/other#Page.DashStyle`), "propertyState": enum (`https://developers.google.com/workspace/slides/api/reference/rest/v1/presentations.pages/other#Page.PropertyState`) } ``` |

| Fields ||
|---|---|
| `outlineFill` | ``object (`https://developers.google.com/workspace/slides/api/reference/rest/v1/presentations.pages/other#Page.OutlineFill`)`` The fill of the outline. |
| `weight` | ``object (`https://developers.google.com/workspace/slides/api/reference/rest/v1/Dimension`)`` The thickness of the outline. |
| `dashStyle` | ``enum (`https://developers.google.com/workspace/slides/api/reference/rest/v1/presentations.pages/other#Page.DashStyle`)`` The dash style of the outline. |
| `propertyState` | ``enum (`https://developers.google.com/workspace/slides/api/reference/rest/v1/presentations.pages/other#Page.PropertyState`)`` The outline property state. Updating the outline on a page element will implicitly update this field to `RENDERED`, unless another value is specified in the same request. To have no outline on a page element, set this field to `NOT_RENDERED`. In this case, any other outline fields set in the same request will be ignored. |

### OutlineFill


The fill of the outline.

| JSON representation |
|---|
| ``` { "solidFill": { object (`https://developers.google.com/workspace/slides/api/reference/rest/v1/presentations.pages/other#Page.SolidFill`) } } ``` |

| Fields ||
|---|---|
| Union field `kind`. The kind of outline fill. `kind` can be only one of the following: ||
| `solidFill` | ``object (`https://developers.google.com/workspace/slides/api/reference/rest/v1/presentations.pages/other#Page.SolidFill`)`` Solid color fill. |

### DashStyle


The kinds of dashes with which linear geometry can be rendered. These values are based on the "ST_PresetLineDashVal" simple type described in section 20.1.10.49 of "Office Open XML File Formats - Fundamentals and Markup Language Reference", part 1 of
[ECMA-376 5th edition](https://ecma-international.org/publications-and-standards/standards/ecma-376/).

| Enums ||
|---|---|
| `DASH_STYLE_UNSPECIFIED` | Unspecified dash style. |
| `SOLID` | Solid line. Corresponds to ECMA-376 ST_PresetLineDashVal value 'solid'. This is the default dash style. |
| `DOT` | Dotted line. Corresponds to ECMA-376 ST_PresetLineDashVal value 'dot'. |
| `DASH` | Dashed line. Corresponds to ECMA-376 ST_PresetLineDashVal value 'dash'. |
| `DASH_DOT` | Alternating dashes and dots. Corresponds to ECMA-376 ST_PresetLineDashVal value 'dashDot'. |
| `LONG_DASH` | Line with large dashes. Corresponds to ECMA-376 ST_PresetLineDashVal value 'lgDash'. |
| `LONG_DASH_DOT` | Alternating large dashes and dots. Corresponds to ECMA-376 ST_PresetLineDashVal value 'lgDashDot'. |

### Shadow


The shadow properties of a page element.


If these fields are unset, they may be inherited from a parent placeholder if it exists. If there is no parent, the fields will default to the value used for new page elements created in the Slides editor, which may depend on the page element kind.

| JSON representation |
|---|
| ``` { "type": enum (`https://developers.google.com/workspace/slides/api/reference/rest/v1/presentations.pages/other#Page.Type_2`), "transform": { object (`https://developers.google.com/workspace/slides/api/reference/rest/v1/presentations.pages/other#Page.AffineTransform`) }, "alignment": enum (`https://developers.google.com/workspace/slides/api/reference/rest/v1/presentations.pages/other#Page.RectanglePosition`), "blurRadius": { object (`https://developers.google.com/workspace/slides/api/reference/rest/v1/Dimension`) }, "color": { object (`https://developers.google.com/workspace/slides/api/reference/rest/v1/presentations.pages/other#Page.OpaqueColor`) }, "alpha": number, "rotateWithShape": boolean, "propertyState": enum (`https://developers.google.com/workspace/slides/api/reference/rest/v1/presentations.pages/other#Page.PropertyState`) } ``` |

| Fields ||
|---|---|
| `type` | ``enum (`https://developers.google.com/workspace/slides/api/reference/rest/v1/presentations.pages/other#Page.Type_2`)`` The type of the shadow. This property is read-only. |
| `transform` | ``object (`https://developers.google.com/workspace/slides/api/reference/rest/v1/presentations.pages/other#Page.AffineTransform`)`` Transform that encodes the translate, scale, and skew of the shadow, relative to the alignment position. |
| `alignment` | ``enum (`https://developers.google.com/workspace/slides/api/reference/rest/v1/presentations.pages/other#Page.RectanglePosition`)`` The alignment point of the shadow, that sets the origin for translate, scale and skew of the shadow. This property is read-only. |
| `blurRadius` | ``object (`https://developers.google.com/workspace/slides/api/reference/rest/v1/Dimension`)`` The radius of the shadow blur. The larger the radius, the more diffuse the shadow becomes. |
| `color` | ``object (`https://developers.google.com/workspace/slides/api/reference/rest/v1/presentations.pages/other#Page.OpaqueColor`)`` The shadow color value. |
| `alpha` | `number` The alpha of the shadow's color, from 0.0 to 1.0. |
| `rotateWithShape` | `boolean` Whether the shadow should rotate with the shape. This property is read-only. |
| `propertyState` | ``enum (`https://developers.google.com/workspace/slides/api/reference/rest/v1/presentations.pages/other#Page.PropertyState`)`` The shadow property state. Updating the shadow on a page element will implicitly update this field to `RENDERED`, unless another value is specified in the same request. To have no shadow on a page element, set this field to `NOT_RENDERED`. In this case, any other shadow fields set in the same request will be ignored. |

### Type


The shadow types.

| Enums ||
|---|---|
| `SHADOW_TYPE_UNSPECIFIED` | Unspecified shadow type. |
| `OUTER` | Outer shadow. |

### RectanglePosition


Defines reference positions in a rectangle.

| Enums ||
|---|---|
| `RECTANGLE_POSITION_UNSPECIFIED` | Unspecified. |
| `TOP_LEFT` | Top left. |
| `TOP_CENTER` | Top center. |
| `TOP_RIGHT` | Top right. |
| `LEFT_CENTER` | Left center. |
| `CENTER` | Center. |
| `RIGHT_CENTER` | Right center. |
| `BOTTOM_LEFT` | Bottom left. |
| `BOTTOM_CENTER` | Bottom center. |
| `BOTTOM_RIGHT` | Bottom right. |

### ContentAlignment


The types of content alignment.


Derived from a subset of the values of the "ST_TextAnchoringType" simple type in section 20.1.10.60 of "Office Open XML File Formats - Fundamentals and Markup Language Reference", part 1 of
[ECMA-376 5th edition](https://ecma-international.org/publications-and-standards/standards/ecma-376/).

| Enums ||
|---|---|
| `CONTENT_ALIGNMENT_UNSPECIFIED` | An unspecified content alignment. The content alignment is inherited from the parent if it exists. |
| `CONTENT_ALIGNMENT_UNSUPPORTED` | An unsupported content alignment. |
| `TOP` | An alignment that aligns the content to the top of the content holder. Corresponds to ECMA-376 ST_TextAnchoringType 't'. |
| `MIDDLE` | An alignment that aligns the content to the middle of the content holder. Corresponds to ECMA-376 ST_TextAnchoringType 'ctr'. |
| `BOTTOM` | An alignment that aligns the content to the bottom of the content holder. Corresponds to ECMA-376 ST_TextAnchoringType 'b'. |

### Placeholder


The placeholder information that uniquely identifies a placeholder shape.

| JSON representation |
|---|
| ``` { "type": enum (`https://developers.google.com/workspace/slides/api/reference/rest/v1/presentations.pages/other#Page.Type_3`), "index": integer, "parentObjectId": string } ``` |

| Fields ||
|---|---|
| `type` | ``enum (`https://developers.google.com/workspace/slides/api/reference/rest/v1/presentations.pages/other#Page.Type_3`)`` The type of the placeholder. |
| `index` | `integer` The index of the placeholder. If the same placeholder types are present in the same page, they would have different index values. |
| `parentObjectId` | `string` The object ID of this shape's parent placeholder. If unset, the parent placeholder shape does not exist, so the shape does not inherit properties from any other shape. |

### Type


The type of a placeholder shape.


Many of these placeholder types correspond to placeholder IDs from the ECMA-376 standard. For more information, see the description of the "ST_PlaceholderType" type in section 19.7.10 of "Office Open XML File Formats - Fundamentals and Markup Language Reference", part 1 of
[ECMA-376 5th edition](https://ecma-international.org/publications-and-standards/standards/ecma-376/).

| Enums ||
|---|---|
| `NONE` | Default value, signifies it is not a placeholder. |
| `BODY` | Body text. |
| `CHART` | Chart or graph. |
| `CLIP_ART` | Clip art image. |
| `CENTERED_TITLE` | Title centered. |
| `DIAGRAM` | Diagram. |
| `DATE_AND_TIME` | Date and time. |
| `FOOTER` | Footer text. |
| `HEADER` | Header text. |
| `MEDIA` | Multimedia. |
| `OBJECT` | Any content type. |
| `PICTURE` | Picture. |
| `SLIDE_NUMBER` | Number of a slide. |
| `SUBTITLE` | Subtitle. |
| `TABLE` | Table. |
| `TITLE` | Slide title. |
| `SLIDE_IMAGE` | Slide image. |

### ImageProperties


The properties of the
`https://developers.google.com/workspace/slides/api/reference/rest/v1/presentations.pages/images#Page.Image`.

| JSON representation |
|---|
| ``` { "cropProperties": { object (`https://developers.google.com/workspace/slides/api/reference/rest/v1/presentations.pages/other#Page.CropProperties`) }, "transparency": number, "brightness": number, "contrast": number, "recolor": { object (`https://developers.google.com/workspace/slides/api/reference/rest/v1/presentations.pages/other#Page.Recolor`) }, "outline": { object (`https://developers.google.com/workspace/slides/api/reference/rest/v1/presentations.pages/other#Page.Outline`) }, "shadow": { object (`https://developers.google.com/workspace/slides/api/reference/rest/v1/presentations.pages/other#Page.Shadow`) }, "link": { object (`https://developers.google.com/workspace/slides/api/reference/rest/v1/presentations.pages/other#Page.Link`) } } ``` |

| Fields ||
|---|---|
| `cropProperties` | ``object (`https://developers.google.com/workspace/slides/api/reference/rest/v1/presentations.pages/other#Page.CropProperties`)`` The crop properties of the image. If not set, the image is not cropped. This property is read-only. |
| `transparency` | `number` The transparency effect of the image. The value should be in the interval \[0.0, 1.0\], where 0 means no effect and 1 means completely transparent. This property is read-only. |
| `brightness` | `number` The brightness effect of the image. The value should be in the interval \[-1.0, 1.0\], where 0 means no effect. This property is read-only. |
| `contrast` | `number` The contrast effect of the image. The value should be in the interval \[-1.0, 1.0\], where 0 means no effect. This property is read-only. |
| `recolor` | ``object (`https://developers.google.com/workspace/slides/api/reference/rest/v1/presentations.pages/other#Page.Recolor`)`` The recolor effect of the image. If not set, the image is not recolored. This property is read-only. |
| `outline` | ``object (`https://developers.google.com/workspace/slides/api/reference/rest/v1/presentations.pages/other#Page.Outline`)`` The outline of the image. If not set, the image has no outline. |
| `shadow` | ``object (`https://developers.google.com/workspace/slides/api/reference/rest/v1/presentations.pages/other#Page.Shadow`)`` The shadow of the image. If not set, the image has no shadow. This property is read-only. |
| `link` | ``object (`https://developers.google.com/workspace/slides/api/reference/rest/v1/presentations.pages/other#Page.Link`)`` The hyperlink destination of the image. If unset, there is no link. |

### CropProperties


The crop properties of an object enclosed in a container. For example, an
`https://developers.google.com/workspace/slides/api/reference/rest/v1/presentations.pages/images#Page.Image`.


The crop properties is represented by the offsets of four edges which define a crop rectangle. The offsets are measured in percentage from the corresponding edges of the object's original bounding rectangle towards inside, relative to the object's original dimensions.

- If the offset is in the interval (0, 1), the corresponding edge of crop rectangle is positioned inside of the object's original bounding rectangle.
- If the offset is negative or greater than 1, the corresponding edge of crop rectangle is positioned outside of the object's original bounding rectangle.
- If the left edge of the crop rectangle is on the right side of its right edge, the object will be flipped horizontally.
- If the top edge of the crop rectangle is below its bottom edge, the object will be flipped vertically.
- If all offsets and rotation angle is 0, the object is not cropped.


After cropping, the content in the crop rectangle will be stretched to fit its container.

| JSON representation |
|---|
| ``` { "leftOffset": number, "rightOffset": number, "topOffset": number, "bottomOffset": number, "angle": number } ``` |

| Fields ||
|---|---|
| `leftOffset` | `number` The offset specifies the left edge of the crop rectangle that is located to the right of the original bounding rectangle left edge, relative to the object's original width. |
| `rightOffset` | `number` The offset specifies the right edge of the crop rectangle that is located to the left of the original bounding rectangle right edge, relative to the object's original width. |
| `topOffset` | `number` The offset specifies the top edge of the crop rectangle that is located below the original bounding rectangle top edge, relative to the object's original height. |
| `bottomOffset` | `number` The offset specifies the bottom edge of the crop rectangle that is located above the original bounding rectangle bottom edge, relative to the object's original height. |
| `angle` | `number` The rotation angle of the crop window around its center, in radians. Rotation angle is applied after the offset. |

### Recolor


A recolor effect applied on an image.

| JSON representation |
|---|
| ``` { "recolorStops": [ { object (`https://developers.google.com/workspace/slides/api/reference/rest/v1/presentations.pages/other#Page.ColorStop`) } ], "name": enum (`https://developers.google.com/workspace/slides/api/reference/rest/v1/presentations.pages/other#Page.Name`) } ``` |

| Fields ||
|---|---|
| `recolorStops[]` | ``object (`https://developers.google.com/workspace/slides/api/reference/rest/v1/presentations.pages/other#Page.ColorStop`)`` The recolor effect is represented by a gradient, which is a list of color stops. The colors in the gradient will replace the corresponding colors at the same position in the color palette and apply to the image. This property is read-only. |
| `name` | ``enum (`https://developers.google.com/workspace/slides/api/reference/rest/v1/presentations.pages/other#Page.Name`)`` The name of the recolor effect. The name is determined from the `recolorStops` by matching the gradient against the colors in the page's current `https://developers.google.com/workspace/slides/api/reference/rest/v1/presentations.pages#Page.PageProperties.FIELDS.color_scheme`. This property is read-only. |

### ColorStop


A color and position in a gradient band.

| JSON representation |
|---|
| ``` { "color": { object (`https://developers.google.com/workspace/slides/api/reference/rest/v1/presentations.pages/other#Page.OpaqueColor`) }, "alpha": number, "position": number } ``` |

| Fields ||
|---|---|
| `color` | ``object (`https://developers.google.com/workspace/slides/api/reference/rest/v1/presentations.pages/other#Page.OpaqueColor`)`` The color of the gradient stop. |
| `alpha` | `number` The alpha value of this color in the gradient band. Defaults to 1.0, fully opaque. |
| `position` | `number` The relative position of the color stop in the gradient band measured in percentage. The value should be in the interval \[0.0, 1.0\]. |

### Name


The recolor effect names.

| Enums ||
|---|---|
| `NONE` | No recolor effect. The default value. |
| `LIGHT1` | A recolor effect that lightens the image using the page's first available color from its color scheme. |
| `LIGHT2` | A recolor effect that lightens the image using the page's second available color from its color scheme. |
| `LIGHT3` | A recolor effect that lightens the image using the page's third available color from its color scheme. |
| `LIGHT4` | A recolor effect that lightens the image using the page's fourth available color from its color scheme. |
| `LIGHT5` | A recolor effect that lightens the image using the page's fifth available color from its color scheme. |
| `LIGHT6` | A recolor effect that lightens the image using the page's sixth available color from its color scheme. |
| `LIGHT7` | A recolor effect that lightens the image using the page's seventh available color from its color scheme. |
| `LIGHT8` | A recolor effect that lightens the image using the page's eighth available color from its color scheme. |
| `LIGHT9` | A recolor effect that lightens the image using the page's ninth available color from its color scheme. |
| `LIGHT10` | A recolor effect that lightens the image using the page's tenth available color from its color scheme. |
| `DARK1` | A recolor effect that darkens the image using the page's first available color from its color scheme. |
| `DARK2` | A recolor effect that darkens the image using the page's second available color from its color scheme. |
| `DARK3` | A recolor effect that darkens the image using the page's third available color from its color scheme. |
| `DARK4` | A recolor effect that darkens the image using the page's fourth available color from its color scheme. |
| `DARK5` | A recolor effect that darkens the image using the page's fifth available color from its color scheme. |
| `DARK6` | A recolor effect that darkens the image using the page's sixth available color from its color scheme. |
| `DARK7` | A recolor effect that darkens the image using the page's seventh available color from its color scheme. |
| `DARK8` | A recolor effect that darkens the image using the page's eighth available color from its color scheme. |
| `DARK9` | A recolor effect that darkens the image using the page's ninth available color from its color scheme. |
| `DARK10` | A recolor effect that darkens the image using the page's tenth available color from its color scheme. |
| `GRAYSCALE` | A recolor effect that recolors the image to grayscale. |
| `NEGATIVE` | A recolor effect that recolors the image to negative grayscale. |
| `SEPIA` | A recolor effect that recolors the image using the sepia color. |
| `CUSTOM` | Custom recolor effect. Refer to `recolorStops` for the concrete gradient. |

