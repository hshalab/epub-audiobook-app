# Data Table Controls Design

## Scope

Add consistent client-side controls to data-list tables across the app. Configuration, instructional, and form-layout tables are excluded. The feature applies only to tables explicitly marked as data tables, preventing accidental changes to specialized layouts.

## User Experience

Each supported table gets a shared toolbar with:

- Full-row text search.
- Automatically generated value filters for useful columns with 2-10 distinct values.
- Existing business filters remain available and continue to work.
- Entries per page: 25, 50, 100, or All; default 25.
- Sortable column headers with text, numeric, and date-aware comparison.
- Pagination and a visible result count.

Search and filters reset the current page to page 1. Row actions, links, checkboxes, and form controls remain functional because rows are only reordered or hidden, not recreated.

## Persistence

Only search and filter values are persisted in `localStorage`. The storage key is scoped by the current page and a stable table key, so state from one table cannot affect another. Sort order and entries-per-page are not persisted and use their defaults after reload.

Invalid or unavailable stored values are ignored. Empty search and default filters are removed from storage where practical.

## Table Identification

Templates opt in with a shared data-table class and a stable `data-table-key`. Tables with existing bespoke filtering/pagination must either be adapted to the shared controller or explicitly remain outside the shared selector to avoid conflicting event handlers.

## Automatic Filters

The shared controller inspects table cells and creates filters only for columns that satisfy all of these rules:

- The column has between 2 and 10 distinct normalized text values.
- The column is not an action column.
- The column does not contain checkboxes or other row controls.
- The column is not primarily made of links.
- Cell values are not long free-form content.

Column headers and existing filter controls are used to identify business meaning where available. Automatic filters are additive and do not replace explicit business filters.

## Architecture

Put the shared behavior in a static JavaScript module loaded from the base template, with the minimum shared CSS needed for the toolbar, filter controls, pagination, sort indicators, and responsive layout. Each controller owns one table and maintains the original row order, current query/filter state, sort state, and page state.

The controller pipeline is:

1. Read the table rows and normalize searchable/sortable values.
2. Restore persisted search/filter state for the page/table key.
3. Render toolbar controls and attach listeners.
4. Apply search and filters.
5. Sort the matching rows when requested.
6. Render the selected page and pagination metadata.

## Compatibility

The implementation must preserve existing table-specific behavior, including checkbox selection, inline actions, links, dynamically refreshed tables, and already-supported business filters. Dynamic table content should expose a refresh/reinitialize hook rather than requiring duplicate table logic.

## Testing

Verify manually and with focused JavaScript tests where the repository supports them:

- Search matches any visible cell text.
- Automatic filters are generated only for eligible columns.
- Existing business filters continue to affect rows.
- Text, number, and date sorting work in both directions.
- Entries options and pagination show the correct rows/counts.
- Search/filter state persists per page and table, while sort/entries do not.
- Empty results, invalid stored values, long cells, links, checkboxes, and action columns behave safely.
- Mobile layouts remain usable and row controls continue working.
