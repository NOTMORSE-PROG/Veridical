// BUG-543: words for a list page that does not exist.
export const PAGE_PAST_END_COPY = {
  title: (page: number) => `Page ${page} doesn't exist`,
  body: "This list has fewer pages. Nothing was changed.",
  firstPage: "Go to page 1",
} as const;
