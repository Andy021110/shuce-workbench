import type { ReviewFilters } from './types'

export interface BusinessExample {
  id: string
  title: string
  tag: string
  persona: string
  month: string
  filters: ReviewFilters
  description: string
  objective: string
  focus: string
}

export interface ExampleResult {
  task_id: string
  step: string
  title: string
  columns: string[]
  rows: Record<string,string|number|null>[]
  row_count: number
  analysis: string
  sql: string
  metric_contracts: {label:string;definition:string;version:string}[]
}

export interface BusinessExampleDetail extends BusinessExample {
  previous_month: string
  summary: string
  results: ExampleResult[]
  followups: {title:string;step:string;reason:string;question:string}[]
  reconciliations: {title:string;passed:boolean}[]
  report: {title:string;markdown:string;fact_references:{id:string;label:string;display:string;source_title:string;scope:string}[]}
  source: {database_id:string;name:string;currency:string;url:string;license:string;computed_at:string;note:string}
}
