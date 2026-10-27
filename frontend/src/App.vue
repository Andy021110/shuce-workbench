<script setup lang="ts">
import AnalysisScope from "./components/AnalysisScope.vue"
import ExecutionDetails from './components/ExecutionDetails.vue'
import AgentCallProgress from './components/AgentCallProgress.vue'
import { product } from './brand'
const publicDeployment = import.meta.env.VITE_PUBLIC_DEPLOYMENT === 'true'
import TargetComparison from "./components/TargetComparison.vue"
import ReviewGuide from "./components/ReviewGuide.vue"
import ReviewProgress from "./components/ReviewProgress.vue"
import BusinessExamples from "./components/BusinessExamples.vue"
import type { BusinessExample } from "./exampleTypes"
import { draftKey, recoverSubmittedDraft } from './drafts'
import { categoryLabel, statusLabel } from './businessFormat'
import { computed, nextTick, onMounted, ref, watch } from "vue"
import { api } from "./api"
import WorkbenchHome from "./components/WorkbenchHome.vue"
import DatasetManager from "./components/DatasetManager.vue"
import UiIcon from "./components/UiIcon.vue"
import ReportCard from "./components/ReportCard.vue"
import ResultProfile from "./components/ResultProfile.vue"
import ResultTableCard from "./components/ResultTableCard.vue"
import type { AuthUser, MetricSelection, QueryResult, ReportDataSource, SavedMemory, SchemaField, SchemaTable, WorkspaceConfig } from "./types"

interface ConversationTurn {
  query: string
  result: QueryResult
}

interface ConversationRecord {
  id: string
  title: string
  updatedAt: number
  turns: ConversationTurn[]
  workspace: WorkspaceConfig
}

interface HistoricalResultTable {
  taskId: string
  title: string
  query: string
  rowCount: number
  columns: string[]
  rows: Record<string, string | number | null>[]
}

const SAVED_TABLE_LIMIT = 8
const FIELD_LIBRARY_LIMIT = 12
function isScopeGuard(result:QueryResult){return result.status==='failed' && Boolean(result.execution_log?.some(event=>event.stage==='scope_inheritance'))}
const historySearch=ref('')
const readingMode=ref(false)
async function toggleReading(){readingMode.value=!readingMode.value;await nextTick();if(!readingMode.value)document.querySelector<HTMLTextAreaElement>('.composer textarea')?.focus()}
const naming=ref(false),nameDraft=ref(''),nameBusy=ref(false),nameError=ref(''),nameNotice=ref('')
function beginRename(){if(!activeConversation.value?.turns.length)return;nameDraft.value=activeConversation.value.title;nameError.value='';nameNotice.value='';naming.value=true}
async function renameAnalysis(){
  if(!activeConversation.value || !nameDraft.value.trim())return
  nameBusy.value=true;nameError.value=''
  try{const result=await api.renameSession(activeConversation.value.id,currentDatabase.value,nameDraft.value.trim());activeConversation.value.title=result.title;naming.value=false;nameNotice.value='分析名称已保存。'}catch(e){nameError.value=String(e)}finally{nameBusy.value=false}
}
const input = ref("")
const loading = ref(false)
const pendingQuery = ref("")
const error = ref("")
const schema = ref<SchemaTable[]>([])
const savedMemories = ref<SavedMemory[]>([])
const workspace = ref<WorkspaceConfig>({})
const currentDatabase = computed(() => workspace.value.database_id || (schema.value.some(table => table.database === "olist_ecommerce") ? "olist_ecommerce" : "short_video_ops"))
const isOlist = computed(() => currentDatabase.value === "olist_ecommerce")
const databases = computed(() => [...new Set(schema.value.map(table => table.database))])
const currentSchema = computed(() => schema.value.filter(table => table.database === currentDatabase.value))
const view = ref("workbench")
const activeJob = ref<any>(null)
const failedJob = ref<any>(null)
const taskStage = ref("")
const jobElapsed = ref(0)
let pollingVersion = 0
const stageLabels: Record<string,string> = {queued:"等待处理",metric_sql:"按标准指标口径执行并统计完整结果",preprocess:"理解问题与会话上下文",retrieve_schema:"检索字段与关联关系",prepare_single_database:"生成并执行只读 SQL",execute_single_database:"核验查询结果",result_analysis:"生成结果说明",answer_qa:"分析已有结果",respond_directly:"整理回复",human_clarification:"等待补充口径",run_multi_database:"执行跨库分析"}
const currentConversations = computed(() => conversations.value.filter(c => c.workspace.database_id === currentDatabase.value))
const recentConversations = computed(() => currentConversations.value.filter(c => c.turns.length))
const conversations = ref<ConversationRecord[]>([])
const activeConversationId = ref("")
watch([view,currentDatabase,activeConversationId],()=>{readingMode.value=false})
const expandedSql = ref<string | null>(null)
const leftOpen = ref(false)
const rightOpen = ref(false)
const conversationScroll = ref<HTMLElement | null>(null)
const authUser = ref<AuthUser | null>(null)
const authReady = ref(false)
const draftNotice=ref('')
const loginLoading = ref(false)
const loginUsername = ref(publicDeployment ? "analyst" : "admin")
const loginPassword = ref(publicDeployment ? "" : "admin123")

const activeConversation = computed(() =>
  conversations.value.find((item) => item.id === activeConversationId.value && item.workspace.database_id===currentDatabase.value),
)
const composerDraftKey=computed(()=>draftKey(authUser.value?.user_id,currentDatabase.value,activeConversationId.value))
watch(composerDraftKey,key=>{
  try{input.value=key ? localStorage.getItem(key) || '' : '';draftNotice.value=''}catch{input.value='';draftNotice.value='草稿暂时只能保留在本页。'}
},{flush:'sync'})
watch(input,value=>{
  const key=composerDraftKey.value
  if(!key)return
  try{if(value)localStorage.setItem(key,value);else localStorage.removeItem(key)}catch{draftNotice.value='草稿暂未保存到此浏览器，请保留当前页面。'}
},{flush:'sync'})
const activeReview = computed(() => [...(activeConversation.value?.turns || [])].reverse().find(t=>t.result.route==='database_query' && t.result.status==='completed')?.result.review_context)
const reviewOverview = computed(()=>[...(activeConversation.value?.turns || [])].reverse().find(t=>t.result.review_context?.month===activeReview.value?.month && t.result.review_context?.step==='overview')?.result.rows)
const confirmedFields = computed(() => workspace.value.schema_fields ?? [])
const analysisTableIds = computed(() => workspace.value.analysis_table_ids ?? [])
const historicalResultTables = computed<HistoricalResultTable[]>(() => {
  return savedMemories.value
    .filter((item) => item.kind === "result_table" && item.task_id && item.rows?.length && currentConversations.value.some(c => c.turns.some(t => t.result.task_id === item.task_id && t.result.status==='completed')))
    .map((item) => ({
      taskId: item.task_id!,
      title: item.title || "查询结果",
      query: item.query || "",
      rowCount: item.rows?.length || 0,
      columns: item.columns || [],
      rows: item.rows || [],
    }))
    .slice(0, SAVED_TABLE_LIMIT)
})
const reportDataSources = computed<ReportDataSource[]>(() => {
  const sources = new Map<string, ReportDataSource>()
  for (const conversation of currentConversations.value) {
    for (const turn of conversation.turns) {
      if (turn.result.status!=='completed' || !turn.result.rows.length) continue
      const temporal=turn.result.result_profile?.temporal
      if(temporal && turn.result.result_profile?.coverage==='full_result') {
        sources.set(turn.result.task_id+':summary:time',{taskId:turn.result.task_id+':summary:time',title:(turn.result.result_title || '查询结果')+' · 完整结果期间汇总',columns:['period',...temporal.measure_columns],rows:temporal.buckets,totalRows:temporal.buckets.length,previewOnly:false,coverage:'full_result_summary',underlyingRows:turn.result.result_profile.analyzed_rows,excludedRows:temporal.missing_dates,currency:conversation.workspace.database_id==='olist_ecommerce'?'BRL':undefined})
      }
      sources.set(turn.result.task_id, {
        taskId: turn.result.task_id,
        title: turn.result.result_title || "查询结果",
        columns: turn.result.columns,
        rows: turn.result.rows,
        totalRows: turn.result.total_rows,
        previewOnly: turn.result.truncated,
        currency:conversation.workspace.database_id==='olist_ecommerce'?'BRL':undefined,
      })
    }
  }
  for (const table of historicalResultTables.value) {
    if (sources.has(table.taskId)) continue
    sources.set(table.taskId, {
      taskId: table.taskId,
      title: table.title,
      columns: table.columns,
      rows: table.rows,
    })
  }
  return [...sources.values()]
})
const selectedAnalysisTables = computed(() =>
  analysisTableIds.value
    .map((id) => historicalResultTables.value.find((table) => table.taskId === id))
    .filter((table): table is HistoricalResultTable => Boolean(table)),
)
const recentFields = computed(() => {
  const items: { field: SchemaField; tableId: string }[] = []
  const keys = new Set<string>()
  const add = (fieldName: string, tableId: string) => {
    const table = schema.value.find((item) => item.id === tableId)
    const field = table?.fields.find((item) => item.name === fieldName)
    const key = `${tableId}:${fieldName}`
    if (field && !keys.has(key)) {
      keys.add(key)
      items.push({ field, tableId })
    }
  }
  for (const conversation of currentConversations.value) {
    for (const turn of [...conversation.turns].reverse()) {
      for (const field of turn.result.schema_graph?.fields ?? []) {
        if (field.source !== "relation_key") add(field.name, field.table_id)
      }
    }
    for (const field of conversation.workspace.schema_fields ?? conversation.workspace.fields ?? []) {
      const tableId = field.tableId
      if (tableId) add(field.name, tableId)
    }
  }
  return items.slice(0, FIELD_LIBRARY_LIMIT)
})
const savedFieldKeys = computed(() => new Set(
  savedMemories.value
    .filter((item) => item.kind === "schema_field")
    .map((item) => `${item.table_id}:${item.name}`),
))
const fieldLibrary = computed(() => {
  const items: { field: SchemaField; tableId: string; saved: boolean }[] = []
  const keys = new Set<string>()
  const add = (field: SchemaField, tableId: string, saved: boolean) => {
    const key = `${tableId}:${field.name}`
    if (keys.has(key)) return
    keys.add(key)
    items.push({ field, tableId, saved })
  }
  for (const memory of savedMemories.value) {
    if (memory.kind !== "schema_field" || !memory.table_id || !memory.name || !currentSchema.value.some(t => t.id === memory.table_id)) continue
    const field = findField(memory.name, memory.table_id) ?? {
      name: memory.name,
      label: memory.label || memory.name,
      type: memory.field_type || "文本",
    }
    add(field, memory.table_id, true)
  }
  for (const item of recentFields.value) {
    add(item.field, item.tableId, savedFieldKeys.value.has(`${item.tableId}:${item.field.name}`))
  }
  return items.slice(0, FIELD_LIBRARY_LIMIT)
})
const historyGroups = computed(() => {
  const today = new Date().toDateString()
  const recent: ConversationRecord[] = []
  const earlier: ConversationRecord[] = []
  const search=historySearch.value.trim().toLocaleLowerCase()
  for (const item of currentConversations.value) {
    if(search && ![item.title,...item.turns.flatMap(t=>[t.query,t.result.result_title || ''])].some(text=>text.toLocaleLowerCase().includes(search)))continue
    if (new Date(item.updatedAt).toDateString() === today) recent.push(item)
    else earlier.push(item)
  }
  return [
    { label: "今天", items: recent },
    { label: "更早", items: earlier },
  ].filter((group) => group.items.length)
})
const userRoleLabel = computed(() => {
  if (authUser.value?.role === "admin") return "全部数据权限"
  if (authUser.value?.role === "growth_ops") return "用户增长数据"
  if (authUser.value?.role === "channel_ops") return "渠道投放数据"
  if (authUser.value?.role === "content_ops") return "内容运营数据"
  return "演示数据"
})

onMounted(async () => {
  if (api.hasSession()) {
    try {
      authUser.value = await api.me()
    } catch {
      api.clearSession()
    }
  }
  authReady.value = true
  if (authUser.value) await initializeUserWorkspace()
})

async function initializeUserWorkspace() {
  historySearch.value='';naming.value=false;nameError.value='';nameNotice.value=''
  conversations.value = []
  activeConversationId.value = ""
  workspace.value = {}
  schema.value = []
  savedMemories.value = []
  try {
    const [schemaResult, memories, records, jobs] = await Promise.all([api.schema(), api.memories(), api.sessions(), api.jobs()])
    schema.value = schemaResult
    savedMemories.value = memories
    conversations.value = records
    const previous = sessionStorage.getItem(`askdata-active-${authUser.value?.user_id}`)
    if (previous && records.some(c => c.id === previous)) selectConversation(previous,sessionStorage.getItem(`askdata-active-db-${authUser.value?.user_id}`) || undefined)
    else {createConversation(); view.value="workbench"}
    if (jobs.length) {
      const job=jobs[0]
      if (!conversations.value.some(c=>c.id===job.session_id)) conversations.value.unshift({id:job.session_id,title:job.query.slice(0,24),updatedAt:Date.now(),turns:[],workspace:{database_id:job.database_id}})
      selectConversation(job.session_id,job.database_id)
      resumeJob(job)
    }
  } catch (caught) {
    createConversation()
    error.value = caught instanceof Error ? caught.message : "工作区加载失败"
  }
}

function onComposerKey(event: KeyboardEvent) {
  if (event.key === "Enter" && !event.shiftKey && !event.isComposing && event.keyCode !== 229) {
    event.preventDefault()
    submit()
  }
}

function useMockAccount(username: string, password: string) {
  loginUsername.value = username
  loginPassword.value = password
  error.value = ""
}

async function loginUser() {
  if (!loginUsername.value.trim() || !loginPassword.value || loginLoading.value) return
  loginLoading.value = true
  error.value = ""
  try {
    authUser.value = await api.login(loginUsername.value.trim(), loginPassword.value)
    await initializeUserWorkspace()
  } catch (caught) {
    error.value = caught instanceof Error ? caught.message : "登录失败"
  } finally {
    loginLoading.value = false
  }
}

async function logoutUser() {
  if (loading.value) return
  try {
    await api.logout()
  } finally {
    pollingVersion++
    activeJob.value=null
    failedJob.value=null
    authUser.value = null
    conversations.value = []
    activeConversationId.value = ""
    workspace.value = {}
    schema.value = []
    savedMemories.value = []
    input.value = ""
    error.value = ""
  }
}

function normalizeWorkspace(value?: WorkspaceConfig): WorkspaceConfig {
  const fields = value?.schema_fields ?? value?.fields ?? []
  return {
    database_id: value?.database_id,
    schema_fields: Array.isArray(fields)
      ? fields.map((field) => ({ name: field.name, tableId: field.tableId, aggregation: "auto" as const }))
      : [],
    analysis_table_ids: Array.isArray(value?.analysis_table_ids)
      ? [...value.analysis_table_ids]
      : [],
  }
}

function tidyConversations() {
  conversations.value.sort((a, b) => b.updatedAt - a.updatedAt)
}

function createConversation() {
  if (loading.value) return
  const databaseId = currentDatabase.value
  const existingEmpty = conversations.value.find((item) => !item.turns.length && item.workspace.database_id === databaseId)
  if (existingEmpty) {
    selectConversation(existingEmpty.id)
    return
  }
  const item: ConversationRecord = {
    id: crypto.randomUUID(),
    title: "新对话",
    updatedAt: Date.now(),
    turns: [],
    workspace: { database_id: databaseId },
  }
  conversations.value.unshift(item)
  activeConversationId.value = item.id
  workspace.value = { database_id: databaseId }
  error.value = ""
  leftOpen.value = false
  tidyConversations()
  view.value="workbench"
  sessionStorage.removeItem(`askdata-active-${authUser.value?.user_id}`)
}

function changeDatabase(event: Event) {
  if (loading.value) return
  const databaseId = (event.target as HTMLSelectElement).value
  if (!databases.value.includes(databaseId)) return
  saveActiveConversation()
  workspace.value = { database_id: databaseId }
  createConversation()
}

function selectConversation(id: string,databaseId?:string) {
  if (loading.value) return
  view.value="analysis"
  sessionStorage.setItem(`askdata-active-${authUser.value?.user_id}`,id)
  const item = conversations.value.find((conversation) => conversation.id === id && conversation.workspace.database_id===(databaseId || currentDatabase.value)) || (!databaseId?conversations.value.find(c=>c.id===id):undefined)
  if (!item) return
  naming.value=false;nameError.value='';nameNotice.value=''
  activeConversationId.value = id
  workspace.value = normalizeWorkspace(item.workspace)
  sessionStorage.setItem(`askdata-active-db-${authUser.value?.user_id}`,workspace.value.database_id || '')
  error.value = ""
  leftOpen.value = false
  nextTick(() => conversationScroll.value?.scrollTo({ top: conversationScroll.value.scrollHeight }))
}

async function removeConversation(id: string) {
  if (loading.value) return
  const databaseId=currentDatabase.value
  try {await api.deleteSession(id,databaseId)} catch(e) {error.value=String(e);return}
  conversations.value = conversations.value.filter((item) => item.id !== id || item.workspace.database_id!==databaseId)
  if (activeConversationId.value === id) {
    if (conversations.value[0]) selectConversation(conversations.value[0].id)
    else createConversation()
  }
  tidyConversations()
}

function saveActiveConversation() {
  const conversation = activeConversation.value
  if (!conversation) return
  conversation.workspace = normalizeWorkspace(workspace.value)
  conversation.updatedAt = Date.now()
  if (conversation.title === "新对话" && conversation.turns[0]) {
    conversation.title = conversation.turns[0].query.slice(0, 18)
  }
  tidyConversations()
}

async function datasetImported(databaseId: string) {
  schema.value=await api.schema()
  workspace.value={database_id:databaseId}
  createConversation()
  view.value="datasets"
}

async function resumeJob(job: any, submitted?:string, submittedKey=composerDraftKey.value) {
  if (!loading.value) selectConversation(job.session_id,job.database_id)
  const current=++pollingVersion
  failedJob.value=null;activeJob.value=job; loading.value=true;pendingQuery.value=job.query;view.value="analysis"
  try {
    while(current===pollingVersion) {
      const state=await api.job(job.id)
      if(current!==pollingVersion) return
      activeJob.value=state;taskStage.value=state.stage;jobElapsed.value=Math.max(0,Math.floor(Date.now()/1000-state.started_at))
      if(state.status!=="running") {
        if(state.result) {
          const conversation=conversations.value.find(c=>c.id===state.session_id && c.workspace.database_id===state.database_id)
          const turn=conversation?.turns.find(t=>t.result.task_id===state.result.task_id)
          if(turn) turn.result=state.result
          else conversation?.turns.push({query:state.query,result:state.result})
          saveActiveConversation()
          if(state.result.status==='failed' && submittedKey===composerDraftKey.value) input.value=recoverSubmittedDraft(input.value,submitted)
        } else {failedJob.value=state;error.value=state.error || "任务失败，可重试";if(submittedKey===composerDraftKey.value)input.value=recoverSubmittedDraft(input.value,submitted)}
        activeJob.value=null
        break
      }
      await new Promise(resolve=>setTimeout(resolve,1000))
    }
  } catch(e) {error.value=String(e);if(submittedKey===composerDraftKey.value)input.value=recoverSubmittedDraft(input.value,submitted)}
  finally {if(current===pollingVersion){loading.value=false;pendingQuery.value=""}}
}
function stopWaiting() {
  pollingVersion++;loading.value=false;pendingQuery.value=""
  error.value="已停止本页等待。后端分析仍会继续并保存记录；可恢复查看进度。"
}
async function submit(text?: string, extra:Partial<WorkspaceConfig>={}) {
  const query=(text ?? input.value).trim()
  if(!query || loading.value || activeJob.value) return
  if(!activeConversation.value) createConversation()
  const submitted=text===undefined?query:undefined
  const submittedKey=composerDraftKey.value
  view.value="analysis";error.value="";loading.value=true;pendingQuery.value=query
  if(submitted)input.value=""
  sessionStorage.setItem(`askdata-active-${authUser.value?.user_id}`,activeConversation.value!.id)
  try {
    const job=await api.startJob(query, {...workspace.value,...extra}, activeConversation.value!.id)
    await resumeJob(job,submitted,submittedKey)
  } catch(e) {error.value=String(e);if(submittedKey===composerDraftKey.value)input.value=recoverSubmittedDraft(input.value,submitted);loading.value=false;pendingQuery.value=""}
}
function confirmContinuation(turn:ConversationTurn){submit(input.value.trim()===turn.query.trim()?undefined:turn.query,{continuation_task_id:turn.result.continuation!.source_task_id})}

async function submitMetric(selection: MetricSelection, reuseSession=false) {
  if(loading.value || activeJob.value) return
  if(!reuseSession) createConversation()
  if(!activeConversation.value) createConversation()
  view.value='analysis';error.value='';loading.value=true;pendingQuery.value='正在按标准口径计算…'
  sessionStorage.setItem(`askdata-active-${authUser.value?.user_id}`,activeConversation.value!.id)
  try {await resumeJob(await api.startMetricJob(selection,activeConversation.value!.id))}
  catch(e) {error.value=String(e);loading.value=false;pendingQuery.value=''}
}
async function submitReview(month:string, step='overview', reuseSession=false, targets?:import('./types').ReviewTargets, filters?:import('./types').ReviewFilters) {
  if(loading.value || activeJob.value) return
  const effectiveFilters=filters || (reuseSession?activeReview.value?.filters:undefined)
  if(!reuseSession || !activeConversation.value) createConversation()
  view.value='analysis';error.value='';loading.value=true;pendingQuery.value='正在计算月度复盘…'
  sessionStorage.setItem(`askdata-active-${authUser.value?.user_id}`,activeConversation.value!.id)
  try {await resumeJob(await api.startReviewJob(month,step,activeConversation.value!.id,targets,effectiveFilters))}
  catch(e) {error.value=String(e);loading.value=false;pendingQuery.value=''}
}
async function startExample(example:BusinessExample,question?:string){
  if(loading.value || activeJob.value || !isOlist.value)return
  await submitReview(example.month,'overview',false,undefined,example.filters)
  const conversation=activeConversation.value
  if(conversation?.turns.some(t=>t.result.review_context?.step==='overview' && t.result.status==='completed')){
    try{const named=await api.renameSession(conversation.id,currentDatabase.value,example.title+' · 我的分析');conversation.title=named.title;saveActiveConversation()}catch(e){error.value='分析已创建，名称未保存：'+String(e)}
    if(question)input.value=question
  }
}
function jumpToResult(taskId:string){document.getElementById('result-'+taskId)?.scrollIntoView({block:'start',behavior:'auto'})}
async function createFactReport(){
  if(!activeConversation.value || loading.value)return
  error.value='';loading.value=true;pendingQuery.value='整理已保存结果的事实报告'
  try{const result=await api.factReport(activeConversation.value.id,currentDatabase.value);activeConversation.value.turns.push({query:'整理已保存结果的事实报告',result});saveActiveConversation();await nextTick();conversationScroll.value?.scrollTo({top:conversationScroll.value.scrollHeight})}
  catch(e){error.value=String(e)}finally{loading.value=false;pendingQuery.value=''}
}
function createReviewReport(month:string) {
  submit(`请根据 ${month} 月度复盘的已有数据生成经营报告。事实表由后端整理，模型只写定性观察、数据边界、待验证原因和下一步建议。不要重复数值表，也不要把当前结果未查询的维度称为数据源缺失。`)
}
function retryTurn(turn: ConversationTurn){
  if(turn.result.review_context) {submitReview(turn.result.review_context.month,turn.result.review_context.step,true,turn.result.review_context.targets,turn.result.review_context.filters);return}
  const definition=turn.result.metric_contracts?.[0]
  if(turn.result.metric_basis==='canonical' && definition?.parameters) submitMetric({metric_id:definition.id,...definition.parameters},true)
  else submit(turn.query)
}

async function saveResult(result: QueryResult) {
  await api.save(result.task_id)
  result.saved = true
  savedMemories.value = await api.memories()
  saveActiveConversation()
}

async function toggleSavedField(field: SchemaField, tableId: string) {
  const memoryId = `field:${tableId}.${field.name}`
  if (savedFieldKeys.value.has(`${tableId}:${field.name}`)) {
    await api.deleteMemory(memoryId)
  } else {
    await api.saveField(tableId, field)
  }
  savedMemories.value = await api.memories()
}

function isSavedField(fieldName: string, tableId?: string) {
  return Boolean(tableId && savedFieldKeys.value.has(`${tableId}:${fieldName}`))
}

function toggleConfirmedFieldMemory(fieldName: string, tableId?: string) {
  const field = findField(fieldName, tableId)
  if (field && tableId) toggleSavedField(field, tableId)
}

function resetWorkspace() {
  workspace.value = { database_id: currentDatabase.value }
  saveActiveConversation()
}

function dragAnalysisTable(event: DragEvent, table: HistoricalResultTable) {
  event.dataTransfer?.setData("analysis-result-table", table.taskId)
  if (event.dataTransfer) event.dataTransfer.effectAllowed = "copy"
}

function addAnalysisTable(taskId: string) {
  if (!historicalResultTables.value.some((table) => table.taskId === taskId) || analysisTableIds.value.includes(taskId)) return
  workspace.value.analysis_table_ids = [...analysisTableIds.value, taskId]
  saveActiveConversation()
}

function dropAnalysisTable(event: DragEvent) {
  addAnalysisTable(event.dataTransfer?.getData("analysis-result-table") ?? "")
}

function removeAnalysisTable(taskId: string) {
  workspace.value.analysis_table_ids = analysisTableIds.value.filter((id) => id !== taskId)
  saveActiveConversation()
}

function dragField(event: DragEvent, field: SchemaField, tableId: string) {
  event.dataTransfer?.setData("field", field.name)
  event.dataTransfer?.setData("field-table", tableId)
  if (event.dataTransfer) event.dataTransfer.effectAllowed = "copy"
}

function findField(fieldName: string, tableId?: string) {
  const selectedTable = tableId ? schema.value.find((table) => table.id === tableId) : undefined
  return selectedTable?.fields.find((field) => field.name === fieldName)
    ?? schema.value.flatMap((table) => table.fields).find((field) => field.name === fieldName)
}

function isNumericField(fieldName: string, tableId?: string) {
  const field = findField(fieldName, tableId)
  return field?.type === "数值" || field?.type === "整数"
}

function addConfirmedField(fieldName: string, sourceTableId?: string) {
  const tableId = sourceTableId
  if (!tableId || !findField(fieldName, tableId)) return
  if (confirmedFields.value.some((field) => field.name === fieldName && field.tableId === tableId)) return
  workspace.value.schema_fields = [
    ...confirmedFields.value,
    {
      name: fieldName,
      aggregation: "auto",
      tableId,
    },
  ]
  saveActiveConversation()
}

function dropConfirmedField(event: DragEvent) {
  addConfirmedField(
    event.dataTransfer?.getData("field") ?? "",
    event.dataTransfer?.getData("field-table") ?? undefined,
  )
}

function removeConfirmedField(fieldName: string, tableId?: string) {
  workspace.value.schema_fields = confirmedFields.value.filter(
    (field) => !(field.name === fieldName && field.tableId === tableId),
  )
  saveActiveConversation()
}

function fieldLabel(fieldName: string, tableId?: string) {
  return findField(fieldName, tableId)?.label ?? fieldName
}

function tableLabel(tableId?: string) {
  return schema.value.find((table) => table.id === tableId)?.label ?? "未知数据表"
}

function clarificationHint(result: QueryResult) {
  return result.clarification?.options.map((option) => option.label).join("、") ?? ""
}

</script>

<template>
  <div v-if="!authReady" class="auth-loading"><span></span><p>正在连接{{ product.name }}…</p></div>

  <main v-else-if="!authUser" class="login-page">
    <section class="login-intro">
      <div class="login-brand"><span><UiIcon name="chart" :size="22" /></span><strong>{{ product.name }}</strong></div>
      <div>

        <h1>{{ product.tagline }}</h1>
        <p>查看真实电商历史指标，运行只读查询，保存并继续你的分析。</p>
      </div>
      <small>{{ product.name }} · {{ publicDeployment ? '受限在线体验' : '本地演示工作空间' }}</small>
    </section>

    <section class="login-side">
      <form class="login-card" @submit.prevent="loginUser">
        <header><span class="login-mark"><UiIcon name="chart" :size="21" /></span><div><h2>欢迎回来</h2><p>登录{{ product.name }}</p></div></header>
        <label>
          <span>账号</span>
          <input v-model="loginUsername" autocomplete="username" placeholder="请输入账号">
        </label>
        <label>
          <span>密码</span>
          <input v-model="loginPassword" type="password" autocomplete="current-password" placeholder="请输入密码">
        </label>
        <p v-if="error" class="login-error">{{ error }}</p>
        <button class="login-submit" :disabled="loginLoading" type="submit">
          {{ loginLoading ? "正在登录…" : "登录" }}
        </button>

        <div v-if="!publicDeployment" class="mock-accounts">
          <p>演示账号</p>
          <button type="button" @click="useMockAccount('admin', 'admin123')">
            <span class="account-icon admin">管</span>
            <span><strong>admin</strong><small>管理员 · 全部数据</small></span>
            <code>admin123</code>
          </button>
          <button type="button" @click="useMockAccount('growth', 'growth123')">
            <span class="account-icon user">增</span>
            <span><strong>growth</strong><small>用户增长 · 18张表</small></span>
            <code>growth123</code>
          </button>
          <button type="button" @click="useMockAccount('channel', 'channel123')">
            <span class="account-icon mock">渠</span>
            <span><strong>channel</strong><small>渠道投放 · 16张表</small></span>
            <code>channel123</code>
          </button>
          <button type="button" @click="useMockAccount('content', 'content123')">
            <span class="account-icon mock">内</span>
            <span><strong>content</strong><small>内容运营 · 19张表</small></span>
            <code>content123</code>
          </button>
        </div>
        <small class="login-note">{{ publicDeployment ? '请使用单独提供的体验账号。自由问数会真实调用模型，请围绕一个具体业务问题开始。' : '选择一个演示角色，体验不同的数据访问范围。' }}</small>
      </form>
    </section>
  </main>

  <div v-else class="app-layout" @keydown.esc="leftOpen=false;rightOpen=false">
    <aside class="history-sidebar" :class="{ open: leftOpen }">
      <div class="brand-row">
        <span class="brand-symbol"><UiIcon name="chart" :size="22" /></span>
        <div><strong>{{ product.name }}</strong><small>{{ product.tagline }}</small></div>
      </div>

      <button class="nav-workbench" :aria-current="view === 'workbench' ? 'page' : undefined" :disabled="loading" @click="createConversation();view='workbench';leftOpen=false">经营概览</button>
      <button v-if="isOlist" class="nav-workbench" :aria-current="view==='examples'?'page':undefined" :disabled="loading || !!activeJob" @click="view='examples';leftOpen=false;rightOpen=false">业务场景示例</button>
      <button class="nav-workbench" :disabled="loading" @click="view='datasets';leftOpen=false">数据接入与管理</button>
      <button class="new-chat" :disabled="loading" @click="createConversation"><UiIcon name="plus" :size="18" />新建分析</button>

      <div class="workspace-label"><UiIcon name="layers" :size="15" />分析记录</div>
      <label class="history-search">搜索分析<input v-model="historySearch" type="search" placeholder="名称或问题关键词" aria-label="搜索分析记录"></label>
      <nav class="history-list" aria-label="对话历史">
        <p v-if="historySearch && !historyGroups.length" class="empty-history" role="status">没有找到匹配的分析，试试其他关键词。</p>
        <section v-for="group in historyGroups" :key="group.label">
          <p>{{ group.label }}</p>
          <div
            v-for="item in group.items"
            :key="item.id"
            class="history-item"
            :class="{ active: view==='analysis' && item.id === activeConversationId }"
          >
            <button class="history-select" :disabled="loading" @click="selectConversation(item.id)">
              <UiIcon name="message" :size="16" />
              <span><strong :title="item.title">{{ item.title }}</strong><small>{{ item.turns.length }} 轮对话</small></span>
            </button>
            <button class="history-remove" :disabled="loading || activeJob?.session_id === item.id" aria-label="删除对话" @click="removeConversation(item.id)">×</button>
          </div>
        </section>
      </nav>

      <div class="history-footer">
        <div class="user-avatar">{{ authUser.display_name.slice(0, 1) }}</div>
        <div class="history-user"><strong>{{ authUser.display_name }}</strong><small><i></i> {{ userRoleLabel }}</small></div>
        <button class="logout-button" aria-label="退出登录" title="退出登录" @click="logoutUser"><UiIcon name="logout" :size="17" /></button>
      </div>
    </aside>

    <button v-if="leftOpen || rightOpen" class="panel-scrim" aria-label="关闭侧边栏" @click="leftOpen=false;rightOpen=false"></button>
    <main class="chat-column" :class="{'reading-mode':readingMode}">
      <header class="chat-header">
        <button class="mobile-menu" aria-label="打开分析记录" @click="leftOpen = !leftOpen"><UiIcon name="menu" /></button>
        <div class="header-breadcrumb"><span>工作空间</span><UiIcon name="chevron" :size="13" /><strong>{{ product.tagline }}</strong></div>
        <span class="model-state"><i></i>{{ currentSchema.length }} 张可用数据表</span>
        <button class="config-trigger" :aria-expanded="rightOpen" @click="rightOpen = !rightOpen"><UiIcon name="layers" :size="16" />数据上下文</button>
      </header>

      <div class="dataset-strip">
        <label>数据源<select aria-label="选择分析数据源" :value="currentDatabase" :disabled="loading || !databases.length" @change="changeDatabase"><option v-for="database in databases" :key="database" :value="database">{{ database === 'olist_ecommerce' ? 'Olist · 真实电商订单' : database === 'short_video_ops' ? '短视频运营 · 合成样例' : database }}</option></select></label>
        <span v-if="isOlist">2016—2018 历史数据 · 金额 BRL</span><span v-else-if="currentDatabase === 'short_video_ops'">短视频运营合成示例数据</span><span v-else>管理员导入的本地 CSV</span>
        <a v-if="isOlist" href="https://www.kaggle.com/datasets/olistbr/brazilian-ecommerce" target="_blank" rel="noreferrer">数据来源 ↗</a>
      </div>

      <section ref="conversationScroll" class="conversation-scroll">
        <div :key="activeConversationId" class="conversation-view">
        <DatasetManager v-if="view === 'datasets'" :admin="authUser.role === 'admin'" @close="view='workbench'" @imported="datasetImported" />
        <BusinessExamples v-else-if="view==='examples' && isOlist" :busy="loading || !!activeJob" @start="startExample" />
        <WorkbenchHome v-else-if="view === 'workbench' || (!activeConversation?.turns.length && !loading)" :key="currentDatabase" :database="currentDatabase" :table-count="currentSchema.length" :recent="recentConversations" :busy="loading" @examples="view='examples';rightOpen=false" @open="selectConversation" @metric="submitMetric" @review="(month,targets,filters)=>submitReview(month,'overview',false,targets,filters)" />
        <div v-else class="thread">
          <section v-if="activeConversation?.turns.length" class="analysis-name" aria-label="分析名称">
            <template v-if="!naming"><h1 :title="activeConversation.title">{{ activeConversation.title }}</h1><button type="button" :disabled="loading" @click="beginRename">重命名分析</button></template>
            <form v-else @submit.prevent="renameAnalysis"><label for="analysis-title">分析名称<input id="analysis-title" v-model="nameDraft" maxlength="80" required :disabled="nameBusy"></label><button type="submit" :disabled="nameBusy || !nameDraft.trim()">保存名称</button><button type="button" :disabled="nameBusy" @click="naming=false">取消重命名</button><p v-if="nameError" role="alert">{{ nameError }}</p></form>
            <p v-if="nameNotice" role="status">{{ nameNotice }}</p>
          </section>
          <ReviewProgress v-if="activeReview && activeConversation" :turns="activeConversation.turns" :review="activeReview" :busy="loading || !!activeJob" @run="step=>submitReview(activeReview!.month,step,true)" @report="createReviewReport(activeReview!.month)" @jump="jumpToResult" />
          <article
            v-for="(turn, turnIndex) in activeConversation?.turns" :id="`result-${turn.result.task_id}`" :key="`${turn.result.task_id}:${turnIndex}`" class="turn">
            <div class="user-message">
              <div class="message-avatar user">你</div>
              <div class="message-body"><small>你</small><p>{{ turn.query }}</p></div>
            </div>

            <div class="assistant-message">
              <div class="message-avatar assistant"><UiIcon name="chart" :size="18" /></div>
              <div class="message-body assistant-body">
                <div class="answer-heading" :class="{ 'qa-heading': turn.result.route !== 'database_query' }">
                  <div><small>{{ product.name }}</small><strong v-if="turn.result.route === 'database_query'">{{ isScopeGuard(turn.result) ? "需要确认分析范围" : turn.result.status === "failed" ? "处理失败" : turn.result.status === "waiting_clarification" ? "需要补充信息" : "查询完成" }}</strong></div>
                  <span v-if="turn.result.route === 'database_query' && turn.result.status === 'completed'">
                    只读数据查询
                  </span>
                </div>

                <div v-if="turn.result.status === 'waiting_clarification'" class="natural-clarification">
                  <p>{{ turn.result.clarification?.question }}</p>
                  <small>{{ turn.result.clarification?.reason }}</small>
                  <span v-if="clarificationHint(turn.result)">请直接回复：{{ clarificationHint(turn.result) }}</span>
                </div>

                <ExecutionDetails :result="turn.result" />
                <details v-if="turn.result.retrieval && turn.result.status !== 'waiting_clarification'" class="execution-details">
                  <summary>查看检索过程</summary>
                  <div class="execution-strip">
                    <span>BM25 {{ turn.result.retrieval.bm25_count }} · Dense {{ turn.result.retrieval.dense_count }}</span>
                    <span>RRF {{ turn.result.retrieval.rrf_count }} → Rerank {{ turn.result.retrieval.selected_count }} 字段</span>
                    <span>阈值 ≥ {{ turn.result.retrieval.threshold.toFixed(2) }}</span>
                    <span v-if="turn.result.schema_graph">Schema 图 {{ turn.result.schema_graph.tables.length }} 表 · {{ turn.result.schema_graph.fields.length }} 字段</span>
                  </div>
                </details>

                <div
                  v-if="turn.result.standalone_query && turn.result.standalone_query !== turn.query"
                  class="standalone-query"
                >
                  <small>本轮独立查询</small>
                  <span>{{ turn.result.standalone_query }}</span>
                </div>

                <div v-if="turn.result.analysis_sources?.length" class="analysis-source-strip">
                  <strong>综合分析来源</strong>
                  <span v-for="source in turn.result.analysis_sources" :key="source.task_id">
                    ▦ {{ source.title }} · {{ source.row_count }} 行
                  </span>
                </div>

                <details v-if="turn.result.interpretation" class="scope-card">
                  <summary class="scope-head"><strong>查看查询口径</strong><span>{{ turn.result.interpretation.time_range }}</span></summary>
                  <div class="scope-grid">
                    <div><small>数据表</small><strong>{{ turn.result.interpretation.table }}</strong></div>
                    <div><small>指标</small><strong>{{ turn.result.interpretation.metric }}</strong></div>
                    <div><small>维度</small><strong>{{ turn.result.interpretation.dimension }}</strong></div>
                    <div><small>时间</small><strong>{{ turn.result.interpretation.time_range }}</strong></div>
                  </div>
                </details>

                <AnalysisScope v-if="turn.result.analysis_scope" :scope="turn.result.analysis_scope" />
                <div v-if="turn.result.analysis_basis" class="basis-notice">
                  <strong>{{ turn.result.analysis_basis.verified ? '统计时间：'+turn.result.analysis_basis.time_label : '此查询未检测到按日期筛选或分组' }}</strong>
                  <span v-if="!turn.result.analysis_basis.verified">如要分析特定月份，请明确日期范围。</span>
                  <span v-else-if="turn.result.analysis_basis.source==='explicit_change'">已按你的要求从{{ turn.result.analysis_basis.previous_time_label }}更改；与此前结果的日期口径不同。</span>
                  <span v-else-if="turn.result.analysis_basis.source==='inherited'">沿用本次分析的时间依据。</span>
                  <span v-else>月份与日期筛选按此字段计算。</span>
                </div>

                <p v-if="turn.result.route === 'data_qa' && turn.result.status==='completed'" class="model-note">{{ turn.result.report_edit ? '报告含人工补充，尚未自动校验；原始报告和数值依据保留。' : turn.result.report?.generation_mode==='backend_facts' ? '后端整理的查询事实，可补充人工经营判断。' : turn.result.report?.generation_mode==='facts_only' ? '以下为后端整理的查询事实，模型经营解读未通过校验。' : '模型生成的分析说明，数据范围以结果与图表标注为准。' }}</p>
                <div v-if="turn.result.route === 'data_qa' && turn.result.status==='failed'" class="natural-clarification">
                  <strong>解读未完成</strong><p>暂未生成完整解读。已完成的查询仍然保留，可重试，或整理事实并人工补充。</p>
                  <button :disabled="loading || !!activeJob" @click="retryTurn(turn)">重试解读</button> <button :disabled="loading || !!activeJob" @click="createFactReport">整理事实并人工补充</button>
                </div>
                <ReportCard
                  v-if="turn.result.report"
                  :report="turn.result.report"
                  :task-id="turn.result.task_id"
                  :draft-owner="authUser?.user_id ? authUser.user_id + ':' + currentDatabase : ''"
                  :edit="turn.result.report_edit"
                  @edited="turn.result.report_edit=$event"
                  :sources="reportDataSources"
                />

                <p v-else-if="turn.result.route !== 'database_query' && turn.result.analysis && turn.result.status!=='failed'" class="qa-answer">{{ turn.result.analysis }}</p>

                <ResultTableCard
                  :currency="isOlist ? 'BRL' : currentDatabase === 'short_video_ops' ? 'CNY' : ''"
                  v-if="turn.result.route === 'database_query' && turn.result.status === 'completed' && turn.result.columns.length"
                  :title="turn.result.result_title || '查询结果'"
                  :columns="turn.result.columns"
                  :rows="turn.result.rows"
                  :task-id="turn.result.task_id"
                  :total-rows="turn.result.total_rows"
                  :snapshot-id="turn.result.snapshot_id"
                >
                  <template #actions>
                      <button v-if="turn.result.sql" @click="expandedSql = expandedSql === turn.result.task_id ? null : turn.result.task_id">SQL</button>
                      <button class="save-result" :disabled="turn.result.saved" @click="saveResult(turn.result)">{{ turn.result.saved ? "已保存" : "保存" }}</button>
                  </template>
                  <template #details>
                    <ResultProfile :profile="turn.result.result_profile" :contracts="turn.result.metric_contracts" :basis="turn.result.metric_basis" />
                    <pre v-if="expandedSql === turn.result.task_id && turn.result.sql"><code>{{ turn.result.sql }}</code></pre>
                  </template>
                </ResultTableCard>

                <p v-if="turn.result.route==='database_query' && turn.result.status==='completed' && turn.result.total_rows===0" class="basis-notice">本次 SQL 返回 0 行。请核对范围和 SQL；这不能单独证明业务数据不存在。</p>
                <details v-if="turn.result.query_validation" class="scope-card"><summary>历史结果需要重新核对</summary><p>{{turn.result.query_validation.reason}}</p><p>{{turn.result.query_validation.original_analysis}}</p><pre><code>{{turn.result.sql}}</code></pre></details>

                <div
                  v-if="turn.result.route === 'database_query' && turn.result.analysis && turn.result.status !== 'waiting_clarification'"
                  class="result-analysis"
                >
                  <strong>{{ isScopeGuard(turn.result) ? '范围保护 · 查询尚未执行' : turn.result.status==='failed' ? '本次未完成' : turn.result.metric_basis === 'canonical' ? '结果说明 · 后端按标准口径计算' : '结果说明 · 模型生成，仅供解读' }}</strong>
                  <p>{{ turn.result.continuation?.can_continue ? '请核对下方订单范围与原问题中的计算规则。确认后可直接继续，不必重新编写完整条件。' : turn.result.analysis }}</p>
                  <div v-if="turn.result.continuation" class="continuation-confirm" role="region" aria-label="确认追问范围">
                    <p v-if="turn.result.continuation.can_continue">确认沿用订单群体，再按这次问题调整。{{turn.result.continuation.intent?.periods?.join('、')}} · {{turn.result.continuation.intent?.filters?.states?.join('、')}} · {{turn.result.continuation.intent?.filters?.categories?.map(categoryLabel).join('、')}} · {{turn.result.continuation.intent?.filters?.order_statuses?.map(statusLabel).join('、')}}</p>
                    <details><summary>核对上次问题与计算规则</summary><p>{{turn.result.continuation.source_query}}</p></details>
                    <button v-if="turn.result.continuation.can_continue" :disabled="loading || !!activeJob" @click="confirmContinuation(turn)">确认范围并继续</button>
                    <p v-else>上次还包含无法自动核对的条件，请参考原问题补全后发送。</p>
                  </div>
                  <button v-if="isScopeGuard(turn.result)" :disabled="loading" @click="createConversation();view='workbench';leftOpen=false">选择月度复盘范围</button>
                  <button v-else-if="turn.result.status === 'failed'" :disabled="loading" @click="retryTurn(turn)">重试此问题</button>
                </div>
                <TargetComparison v-if="turn.result.review_context?.step==='overview'" :items="turn.result.review_context.target_comparison || []" />
                <nav v-if="activeReview && turnIndex===(activeConversation?.turns.length || 0)-1" class="review-actions" aria-label="继续月度复盘">
                  <p>{{ activeReview.month }} 对比 {{ activeReview.previous_month }} · 下单时间 · 商品金额 BRL</p>
                  <ReviewGuide :step="activeReview.step" :busy="loading || !!activeJob" :overview="reviewOverview" @step="step=>submitReview(activeReview!.month,step,true)" />
                  <button :disabled="loading || !!activeJob" @click="createFactReport">整理事实报告</button>
                  <button :disabled="loading || !!activeJob" @click="createReviewReport(activeReview.month)">生成复盘报告</button>
                </nav>
              </div>
            </div>
          </article>

          <div v-if="loading && pendingQuery" class="user-message pending-user-message">
            <div class="message-avatar user">你</div>
            <div class="message-body"><small>你 · 已发送</small><p>{{ pendingQuery }}</p></div>
          </div>

          <div v-if="loading" class="assistant-message loading-message">
            <div class="message-avatar assistant"><UiIcon name="chart" :size="18" /></div>
            <div class="loading-copy"><span></span><div><strong>{{ stageLabels[taskStage] || '等待处理' }}</strong><small>已等待 {{ jobElapsed }} 秒 · 阶段由后端更新</small><AgentCallProgress :trace="activeJob?.execution_trace" :stage="taskStage" /><button @click="stopWaiting">停止本页等待</button></div></div>
          </div>
        </div>
        </div>

        <div v-if="error" class="error-banner" role="alert">{{ error }} <button v-if="activeJob && !loading" @click="resumeJob(activeJob)">恢复查看进度</button></div>
        <AgentCallProgress v-if="failedJob && failedJob.session_id===activeConversationId && failedJob.database_id===currentDatabase" :trace="failedJob.execution_trace" :stage="failedJob.stage" failed />
        <AgentCallProgress v-if="activeJob && !loading && activeJob.session_id===activeConversationId && activeJob.database_id===currentDatabase" :trace="activeJob.execution_trace" :stage="activeJob.stage" paused />
      </section>

      <footer v-if="view !== 'datasets' && view !== 'examples'" class="composer">
        <button v-if="activeConversation?.turns.length" type="button" class="reading-toggle" :aria-expanded="!readingMode" @click="toggleReading">{{ readingMode ? '退出阅读，继续提问' : '收起输入区，专注阅读' }}</button>
        <div v-if="!readingMode && activeJob && !loading" class="detached-job">页面已停止等待，后台任务未取消 <button @click="resumeJob(activeJob)">恢复查看</button></div>
        <div v-show="!readingMode" class="composer-box">
          <textarea v-model="input" rows="2" maxlength="500" :aria-label="`向${product.name}提问`" :placeholder="isOlist ? '输入指标、时间与维度，例如：统计 2017 年每月去重订单数' : currentDatabase === 'short_video_ops' ? '输入指标、时间与维度，例如：统计 2026 年 8 月各渠道新增用户数' : '输入字段、条件与统计口径，开始分析本地 CSV'" @keydown="onComposerKey"></textarea>
          <div class="composer-meta">
            <span>问数 {{ confirmedFields.length }} 个字段 · 分析 {{ selectedAnalysisTables.length }} 张结果表 · {{ input.length }}/500</span>
            <button aria-label="发送问题" :disabled="!input.trim() || loading || !!activeJob" @click="submit()"><UiIcon name="arrow" :size="19" /></button>
          </div>
        </div>
        <small v-show="!readingMode">{{ isOlist ? 'Olist 匿名历史数据 · CC BY-NC-SA 4.0 · BRL' : currentDatabase === 'short_video_ops' ? '短视频运营合成示例数据' : '本地 CSV · 单位以字段说明为准' }} · Enter 发送，Shift + Enter 换行</small>
        <small v-if="draftNotice" role="status">{{draftNotice}}</small>
      </footer>
    </main>

    <aside class="query-panel" :class="{ open: rightOpen }">
      <div class="panel-header">
        <div><p class="kicker">DATA CONTEXT</p><h2>数据上下文</h2></div>
        <div class="panel-controls"><button @click="resetWorkspace">重置</button><button class="context-close" aria-label="关闭数据上下文" @click="rightOpen=false"><UiIcon name="close" :size="17" /></button></div>
      </div>

      <section class="context-block query-context-block">
        <div class="context-heading">
          <div><strong>查询字段</strong><small>用于生成 SQL</small></div>
          <em>{{ confirmedFields.length }} 个</em>
        </div>
        <div
          class="confirmed-fields-zone"
          :class="{ filled: confirmedFields.length }"
          @dragover.prevent
          @drop.prevent="dropConfirmedField"
        >
          <div v-if="!confirmedFields.length" class="empty-confirmation">
            <span>＋</span>
            <strong>添加需要关注的字段</strong>
          </div>
          <div v-for="field in confirmedFields" :key="`${field.tableId}:${field.name}`" class="confirmed-field">
            <span class="field-badge">{{ isNumericField(field.name, field.tableId) ? "#" : "T" }}</span>
            <span class="confirmed-field-name"><strong>{{ fieldLabel(field.name, field.tableId) }}</strong><small>{{ tableLabel(field.tableId) }}</small></span>
            <button class="save-field" :class="{ saved: isSavedField(field.name, field.tableId) }" :aria-label="`保存${fieldLabel(field.name, field.tableId)}`" @click="toggleConfirmedFieldMemory(field.name, field.tableId)">{{ isSavedField(field.name, field.tableId) ? "★" : "☆" }}</button>
            <button class="remove-field" :aria-label="`移除${fieldLabel(field.name, field.tableId)}`" @click="removeConfirmedField(field.name, field.tableId)">×</button>
          </div>
        </div>
      </section>

      <section class="context-block analysis-context-block">
        <div class="context-heading">
          <div><strong>参考结果</strong><small>用于综合分析</small></div>
          <em>{{ selectedAnalysisTables.length }} 张</em>
        </div>
        <div
          class="analysis-tables-zone"
          :class="{ filled: selectedAnalysisTables.length }"
          @dragover.prevent
          @drop.prevent="dropAnalysisTable"
        >
          <div v-if="!selectedAnalysisTables.length" class="empty-confirmation">
            <span>＋</span>
            <strong>添加已保存的查询结果</strong>
          </div>
          <div v-for="table in selectedAnalysisTables" :key="table.taskId" class="analysis-table-chip">
            <span class="table-mark">▦</span>
            <span><strong>{{ table.title }}</strong><small>{{ table.rowCount }} 行 · {{ table.columns.slice(0, 3).join(" / ") }}</small></span>
            <button :aria-label="`移除${table.title}`" @click="removeAnalysisTable(table.taskId)">×</button>
          </div>
        </div>
      </section>

      <section class="recent-library">
        <div class="library-heading">
          <div><strong>字段与结果</strong><small>点击添加，也可拖拽</small></div>
        </div>
        <div class="recent-columns">
          <div class="recent-column">
            <p>字段 · 最多 {{ FIELD_LIBRARY_LIMIT }} 个</p>
            <div v-if="!fieldLibrary.length" class="library-empty">查询后可收藏关键字段</div>
            <div
              v-for="item in fieldLibrary"
              :key="`${item.tableId}:${item.field.name}`"
              class="field-library-row"
              draggable="true"
              @dragstart="dragField($event, item.field, item.tableId)"
            >
              <button class="recent-card field-card" @click="addConfirmedField(item.field.name, item.tableId)">
                <span class="field-badge">{{ item.field.type === "数值" || item.field.type === "整数" ? "#" : "T" }}</span>
                <span><strong>{{ item.field.label }}</strong><small>{{ tableLabel(item.tableId) }}</small></span>
              </button>
              <button class="save-field" :class="{ saved: item.saved }" :aria-label="`${item.saved ? '取消收藏' : '收藏'}${item.field.label}`" @click="toggleSavedField(item.field, item.tableId)">{{ item.saved ? "★" : "☆" }}</button>
            </div>
          </div>
          <div class="recent-column">
            <p>结果表 · 最多 {{ SAVED_TABLE_LIMIT }} 张</p>
            <div v-if="!historicalResultTables.length" class="library-empty">保存查询结果后显示</div>
            <button
              v-for="table in historicalResultTables"
              :key="table.taskId"
              class="recent-card table-card"
              draggable="true"
              @dragstart="dragAnalysisTable($event, table)"
              @click="addAnalysisTable(table.taskId)"
            >
              <span class="table-mark">▦</span>
              <span><strong>{{ table.title }}</strong><small>{{ table.rowCount }} 行 · {{ table.query }}</small></span>
              <em>⋮⋮</em>
            </button>
          </div>
        </div>
      </section>
    </aside>

  </div>
</template>
<style scoped>
.continuation-confirm{margin-top:16px;padding:16px;border:1px solid #b4c9bc;border-radius:10px;background:#f4f9f6;color:#294c39;line-height:1.7}.continuation-confirm summary{min-height:44px;align-content:center;cursor:pointer}.continuation-confirm button{min-height:44px;margin-top:8px;padding:8px 16px;border:1px solid #246b4e;border-radius:8px;background:#246b4e;color:#fff}.continuation-confirm :is(button,summary):focus-visible{outline:3px solid #5a9279;outline-offset:3px}
</style>
