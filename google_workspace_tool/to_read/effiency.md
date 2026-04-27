# Improve performance

This document covers some techniques you can use to improve the performance of your application. In some cases, examples from other APIs or generic APIs are used to illustrate the ideas presented. However, the same concepts are applicable to the Google Docs API.

## Compression using gzip

An easy and convenient way to reduce the bandwidth needed for each request is to enable gzip compression. Although this requires additional CPU time to uncompress the results, the trade-off with network costs usually makes it very worthwhile.

In order to receive a gzip-encoded response you must do two things: Set an `Accept-Encoding` header, and modify your user agent to contain the string `gzip`. Here is an example of properly formed HTTP headers for enabling gzip compression:

```
Accept-Encoding: gzip
User-Agent: my program (gzip)
```

## Working with partial resources

Another way to improve the performance of your API calls is by requesting only the portion of the data that you're interested in. This lets your application avoid transferring, parsing, and storing unneeded fields, so it can use resources including network, CPU, and memory more efficiently.

### Partial response

By default, the server sends back the full representation of a resource after processing requests. For better performance, you can ask the server to send only the fields you really need and get a *partial response* instead.

To request a partial response, use the `fields` request parameter to specify the fields you want returned. You can use this parameter with any request that returns response data.

#### Example

The following example shows the use of the `fields` parameter with a generic (fictional) "Demo" API.

**Simple request:** This HTTP `GET` request omits the `fields` parameter and returns the full resource.

```
https://www.googleapis.com/demo/v1
```

**Full resource response:** The full resource data includes the following fields, along with many others that have been omitted for brevity.

```
{
  "kind": "demo",
  ...
  "items": [
  {
    "title": "First title",
    "comment": "First comment.",
    "characteristics": {
      "length": "short",
      "accuracy": "high",
      "followers": ["Jo", "Will"],
    },
    "status": "active",
    ...
  },
  {
    "title": "Second title",
    "comment": "Second comment.",
    "characteristics": {
      "length": "long",
      "accuracy": "medium"
      "followers": [ ],
    },
    "status": "pending",
    ...
  },
  ...
  ]
}
```

**Request for a partial response:** The following request for this same resource uses the `fields` parameter to significantly reduce the amount of data returned.

```
https://www.googleapis.com/demo/v1?fields=kind,items(title,characteristics/length)
```

**Partial response:** In response to the request above, the server sends back a response that contains only the kind information along with a pared-down items array that includes only HTML title and length characteristic information in each item.

```
200 OK
```

```
{
  "kind": "demo",
  "items": [{
    "title": "First title",
    "characteristics": {
      "length": "short"
    }
  }, {
    "title": "Second title",
    "characteristics": {
      "length": "long"
    }
  },
  ...
  ]
}
```

Note that the response is a JSON object that includes only the selected fields and their enclosing parent objects.

Details on how to format the `fields` parameter is covered next, followed by more details about what exactly gets returned in the response.

#### Fields parameter syntax summary

The format of the `fields` request parameter value is loosely based on XPath syntax. The supported syntax is summarized below, and additional examples are provided in the following section.

- Use a comma-separated list to select multiple fields.
- Use `a/b` to select a field `b` that is nested within field `a`; use `a/b/c` to select a field `c` nested within `b`.   

  **Exception:** For API responses that use "data" wrappers, where the response is nested within a `data` object that looks like `data: { ... }`, do not include "`data`" in the `fields` specification. Including the data object with a fields specification like `data/a/b` causes an error. Instead, just use a `fields` specification like `a/b`.
- Use a sub-selector to request a set of specific sub-fields of arrays or objects by placing expressions in parentheses "`( )`".

  For example: `fields=items(id,author/email)` returns only the item ID and author's email for each element in the items array. You can also specify a single sub-field, where `fields=items(id)` is equivalent to `fields=items/id`.
- Use wildcards in field selections, if needed. For example: `fields=items/pagemap/*` selects all objects in a pagemap.

#### More examples of using the fields parameter

The examples below include descriptions of how the `fields` parameter value affects the response.

**Note:** As with all query parameter values, the `fields` parameter value must be URL encoded. For better readability, the examples in this document omit the encoding.

Identify the fields you want returned, or make field selections.
:   The `fields` request parameter value is a comma-separated list of fields, and each field is specified relative to the root of the response. Thus, if you are performing a list operation, the response is a collection, and it generally includes an array of resources. If you are performing an operation that returns a single resource, fields are specified relative to that resource. If the field you select is (or is part of) an array, the server returns the selected portion of all elements in the array.  

    <br />


    Here are some collection-level examples:  

    | Examples | Effect |
    |---|---|
    | `items` | Returns all elements in the items array, including all fields in each element, but no other fields. |
    | `etag,items` | Returns both the `etag` field and all elements in the items array. |
    | `items/title` | Returns only the `title` field for all elements in the items array. <br /> Whenever a nested field is returned, the response includes the enclosing parent objects. The parent fields do not include any other child fields unless they are also selected explicitly. |
    | `context/facets/label` | Returns only the `label` field for all members of the `facets` array, which is itself nested under the `context` object. |
    | `items/pagemap/*/title` | For each element in the items array, returns only the `title` field (if present) of all objects that are children of `pagemap`. |


    <br />


    Here are some resource-level examples:  

    | Examples | Effect |
    |---|---|
    | `title` | Returns the `title` field of the requested resource. |
    | `author/uri` | Returns the `uri` sub-field of the `author` object in the requested resource. |
    | `links/*/href` | Returns the `href` field of all objects that are children of `links`. |


Request only parts of specific fields using sub-selections.
:   By default, if your request specifies particular fields, the server returns the objects or array elements in their entirety. You can specify a response that includes only certain sub-fields. You do this using "`( )`" sub-selection syntax, as in the example below.

    | Example | Effect |
    |---|---|
    | `items(title,author/uri)` | Returns only the values of the `title` and author's `uri` for each element in the items array. |


<br />

#### Handling partial responses

After a server processes a valid request that includes the `fields` query parameter, it sends back an HTTP `200 OK` status code, along with the requested data. If the `fields` query parameter has an error or is otherwise invalid, the server returns an HTTP `400 Bad Request` status code, along with an error message telling the user what was wrong with their fields selection (for example, `"Invalid field selection a/b"`).

Here is the partial response example shown in the [introductory section](https://developers.google.com/workspace/docs/api/how-tos/performance#partial-response) above. The request uses the `fields` parameter to specify which fields to return.

```
https://www.googleapis.com/demo/v1?fields=kind,items(title,characteristics/length)
```

The partial response looks like this:

```
200 OK
```

```
{
  "kind": "demo",
  "items": [{
    "title": "First title",
    "characteristics": {
      "length": "short"
    }
  }, {
    "title": "Second title",
    "characteristics": {
      "length": "long"
    }
  },
  ...
  ]
}
```

**Note:** For APIs that support query parameters for data pagination (`maxResults` and `nextPageToken`, for example), use those parameters to reduce the results of each query to a manageable size. Otherwise, the performance gains possible with partial response might not be realized.

<br />


# Batch requests

This document shows how to batch API calls together to reduce the number of
connections your client has to make. Batching can improve an application's
efficiency by decreasing network round trips and increasing throughput.

## Overview


Each connection your client makes results in a certain amount of overhead.
The Google Docs API supports batching to let your client place multiple
request objects, each one specifying a single type of request to perform,
into a single batch request. A batch request can boost performance by
combining multiple subrequests into a single call to the server, retrieving
a single response back.


We encourage users to always batch multiple requests together. Here are some
examples of situations where you can use batching:

- You've just started using the API and you have lots of data to upload.
- You need to update metadata or properties, such as formatting, on multiple objects.
- You need to delete many objects.

## Limits, authorization, \& dependency considerations

Here's a list of other items to consider when employing batch updating:

- Each batch request, including all subrequests, is counted as one API request toward your [usage limit](https://developers.google.com/workspace/docs/api/limits).
- A batch request is authenticated once. This single authentication applies to all batch update objects in the request.
- The server processes the subrequests in the same order they appear in the batch request. Latter subrequests can depend on actions taken during earlier subrequests. For example, in the same batch request, users can insert text into an existing document and then style it.

## Batch details


A batch request consists of one `https://developers.google.com/workspace/docs/api/reference/rest/v1/documents/batchUpdate` method call
with multiple subrequests to, for example, add and then format a document.


Each request is validated before being applied. All subrequests in the batch
update are applied atomically. That is, if any request is not valid then the
entire update is unsuccessful and none of the (potentially dependent)
changes are applied.


Some requests provide responses with information about the applied requests.
For example, all batch update requests to add objects return responses so
you can access the metadata of the newly added object, such as the ID or
title.


With this approach, you can build an entire Google document using one API
batch update request with multiple subrequests.

### Format of a batch request


A [request](https://developers.google.com/workspace/docs/api/reference/rest/v1/documents/request) is a single JSON request containing multiple,
nested subrequests with one required property: `requests`. The
requests are constructed in an array of individual requests. Each request uses
JSON to represent the request object and to contain its properties.

### Format of a batch response


The [response](https://developers.google.com/workspace/docs/api/reference/rest/v1/documents/response) format for a batch request is similar to the
request format. The server's response contains a complete reply of the single
response object.


The main JSON object's property is named `replies`. The responses
are returned in an array, with each response to one of the requests occupying
the same index order as the corresponding request. Some requests don't have
responses and the response at that array index is empty.

## Example

The following code sample shows the use of batching with the Docs API.

### Request

This example batch request demonstrates how to:

- Insert "Hello World" text into the start of an existing document, with an
  index `location` of `1`, using the
  [`InsertTextRequest`](https://developers.google.com/workspace/docs/api/reference/rest/v1/documents/request#inserttextrequest).

- Update the word "Hello" using the
  [`UpdateTextStyleRequest`](https://developers.google.com/workspace/docs/api/reference/rest/v1/documents/request#updatetextstylerequest).
  The `startIndex` and `endIndex` define the `range` of formatted text within
  the segment.

- Using `textStyle`, set the font style to bold and the color to blue for just
  the word "Hello".

- Using the [`WriteControl`](https://developers.google.com/workspace/docs/api/reference/rest/v1/documents/batchUpdate#writecontrol)
  field, you can control how write requests are executed. For more
  information, see [Establish state consistency with
  WriteControl](https://developers.google.com/workspace/docs/api/how-tos/best-practices#establish-state-consistency).

```
{
   "requests":[
      {
         "insertText":{
            "location":{
               "index":1,
               "tabId":TAB_ID
            },
            "text":"Hello World"
         }
      },
      {
         "updateTextStyle":{
            "range":{
               "startIndex":1,
               "endIndex":6
            },
            "textStyle":{
               "bold":true,
               "foregroundColor":{
                  "color":{
                     "rgbColor":{
                        "blue":1
                     }
                  }
               }
            },
            "fields":"bold,foreground_color"
         }
      }
   ],
   "writeControl": {
      "requiredRevisionId": "REQUIRED_REVISION_ID"
  }
}
```

Replace <var translate="no">TAB_ID</var> and <var translate="no">REQUIRED_REVISION_ID</var> with
the tab ID and the revision ID, respectively, of the document the write request
is applied to.

### Response

This example batch response displays information on how each subrequest within
the batch request was applied. Neither the
[`InsertTextRequest`](https://developers.google.com/workspace/docs/api/reference/rest/v1/documents/request#InsertTextRequest)
or the
[`UpdateTextStyleRequest`](https://developers.google.com/workspace/docs/api/reference/rest/v1/documents/request#updatetextstylerequest)
contain a response, so the index values of the array at \[0\] and \[1\] consist
of empty curly braces. The batch request displays the `WriteControl` object,
which shows how the requests were executed.

```
{
   "replies":[
      {},
      {}
   ],
   "writeControl":{
      "requiredRevisionId":`REQUIRED_REVISION_ID`
   },
   "documentId":`DOCUMENT_ID`
}
```

## Related topics

- [Best practices for best results](https://developers.google.com/workspace/docs/api/how-tos/best-practices)