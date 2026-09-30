<template>
  <section class="page" data-module="lavatory">
    <header class="page-head">
      <div>
        <h2>清水排污管理</h2>
        <p class="page-desc">班次汇总只由已完成明细实时推导，取消、改量与重复提交都会按同一份明细生效。</p>
      </div>
      <div class="page-actions">
        <button class="btn primary" type="button" @click="openCreate">登记排污任务</button>
        <button class="btn" type="button" :disabled="recalculating" @click="recalculateShifts">按明细重算历史班次</button>
        <button class="btn" type="button" @click="exportRows">导出清水排污清单</button>
      </div>
    </header>

    <div class="stat-row">
      <article v-for="item in summaryCards" :key="item.label" class="stat-card">
        <span class="stat-label">{{ item.label }}</span>
        <strong class="stat-value">{{ item.value }}</strong>
      </article>
    </div>

    <form class="filter-bar" @submit.prevent="reload">
      <label class="filter-item">
        <span>排污编号</span>
        <input v-model="keyword" placeholder="按排污编号检索" />
      </label>
      <label class="filter-item">
        <span>服务状态</span>
        <select v-model="statusFilter">
          <option value="">全部</option>
          <option v-for="status in statuses" :key="status" :value="status">{{ status }}</option>
        </select>
      </label>
      <label class="filter-item">
        <span>完成日期</span>
        <input v-model="summaryDate" type="date" />
      </label>
      <button class="btn" type="submit">查询</button>
      <button class="btn ghost" type="button" @click="resetFilters">重置条件</button>
    </form>

    <section class="panel">
      <h3>班次汇总</h3>
      <table class="data-table">
        <thead>
          <tr>
            <th>日期</th>
            <th>班次</th>
            <th>加注趟数</th>
            <th>清水加注量（升）</th>
            <th>排污趟数</th>
            <th>排污量（升）</th>
          </tr>
        </thead>
        <tbody>
          <tr v-for="shift in shiftRows" :key="`${shift.shift_date}-${shift.shift}`">
            <td>{{ shift.shift_date }}</td>
            <td>{{ shift.shift }}</td>
            <td>{{ shift.water_fill_count }}</td>
            <td>{{ shift.water_amount }}</td>
            <td>{{ shift.waste_discharge_count }}</td>
            <td>{{ shift.waste_amount }}</td>
          </tr>
          <tr v-if="!shiftRows.length">
            <td colspan="6" class="empty-state">当前日期下没有已完成的清水排污服务</td>
          </tr>
        </tbody>
      </table>
    </section>

    <table class="data-table record-table">
      <thead>
        <tr>
          <th v-for="column in columns" :key="column">{{ column }}</th>
          <th>可执行动作</th>
        </tr>
      </thead>
      <tbody>
        <tr v-for="row in rows" :key="String(row.id)">
          <td v-for="column in columns" :key="column">{{ row[column] ?? '—' }}</td>
          <td class="row-actions">
            <button class="link" type="button" @click="openDetail(row)">详情</button>
            <button class="link" type="button" @click="openEdit(row)">修改</button>
            <button
              v-for="action in availableActions(row)"
              :key="action"
              class="link"
              type="button"
              :disabled="busyId === row.id"
              @click="runAction(action, row)"
            >
              {{ action }}
            </button>
          </td>
        </tr>
        <tr v-if="!rows.length">
          <td :colspan="columns.length + 1" class="empty-state">暂无清水排污数据，可先登记排污任务</td>
        </tr>
      </tbody>
    </table>

    <footer class="page-foot">
      <span>共 {{ total }} 条清水排污记录</span>
      <span v-if="successMessage" class="success-text">{{ successMessage }}</span>
      <span v-if="errorMessage" class="error-text">{{ errorMessage }}</span>
    </footer>

    <div v-if="formOpen" class="modal-backdrop" @click.self="closeForm">
      <form class="modal" @submit.prevent="saveForm">
        <h3>{{ formMode === 'create' ? '登记排污任务' : '修改排污任务' }}</h3>
        <label v-for="field in formFields" :key="field.key" class="modal-field">
          <span>{{ field.label }}</span>
          <input
            v-if="field.type !== 'select'"
            v-model="formState[field.key]"
            :type="field.type"
            :required="field.required"
            :min="field.type === 'number' ? '0' : undefined"
            step="0.001"
          />
          <select v-else v-model="formState[field.key]">
            <option value="">未指定</option>
            <option v-for="option in shiftOptions" :key="option" :value="option">{{ option }}</option>
          </select>
        </label>
        <div class="modal-actions">
          <button class="btn" type="button" @click="closeForm">取消</button>
          <button class="btn primary" type="submit" :disabled="saving">{{ saving ? '提交中…' : '保存' }}</button>
        </div>
      </form>
    </div>

    <div v-if="detail" class="modal-backdrop" @click.self="detail = null">
      <div class="modal">
        <h3>排污任务详情</h3>
        <dl class="detail-list">
          <template v-for="field in detailFields" :key="field">
            <dt>{{ field }}</dt>
            <dd>{{ detail[field] ?? '—' }}</dd>
          </template>
        </dl>
        <div class="modal-actions">
          <button class="btn primary" type="button" @click="detail = null">关闭</button>
        </div>
      </div>
    </div>
  </section>
</template>

<script setup lang="ts">
import { computed, onMounted, reactive, ref } from 'vue'

import { request } from '@/api/client'

type Row = Record<string, string | number | boolean | null>
type SummaryShift = {
  shift_date: string
  shift: string
  water_fill_count: number
  waste_discharge_count: number
  water_amount: number
  waste_amount: number
}
type Summary = {
  completed_count: number
  cancelled_count: number
  water_fill_count: number
  waste_discharge_count: number
  water_amount: number
  waste_amount: number
  shifts: SummaryShift[]
}
type FormField = {
  key: keyof typeof initialForm
  label: string
  type: string
  required?: boolean
}

const ENDPOINT = '/api/lavatory'
const columns = ['排污编号', '对应航班', '清水加注量', '排污量', '服务车辆', '操作人员', '完成时间', '服务状态']
const statuses = ['待服务', '服务中', '已完成', '已取消']
const shiftOptions = ['上午', '下午', '夜间']
const detailFields = [...columns, '班次']
const formFields: FormField[] = [
  { key: '排污编号', label: '排污编号', type: 'text', required: true },
  { key: '对应航班', label: '对应航班', type: 'text', required: true },
  { key: '清水加注量', label: '清水加注量（升）', type: 'number', required: true },
  { key: '排污量', label: '排污量（升）', type: 'number' },
  { key: '服务车辆', label: '服务车辆', type: 'text' },
  { key: '操作人员', label: '操作人员', type: 'text' },
  { key: '完成时间', label: '完成时间', type: 'datetime-local' },
  { key: '班次', label: '班次', type: 'select' },
]

const initialForm = {
  排污编号: '',
  对应航班: '',
  清水加注量: '',
  排污量: '',
  服务车辆: '',
  操作人员: '',
  完成时间: '',
  班次: '',
}

const rows = ref<Row[]>([])
const total = ref(0)
const keyword = ref('')
const statusFilter = ref('')
const summaryDate = ref('')
const summary = ref<Summary>({
  completed_count: 0,
  cancelled_count: 0,
  water_fill_count: 0,
  waste_discharge_count: 0,
  water_amount: 0,
  waste_amount: 0,
  shifts: [],
})
const errorMessage = ref('')
const successMessage = ref('')
const busyId = ref<number | null>(null)
const recalculating = ref(false)
const formOpen = ref(false)
const formMode = ref<'create' | 'edit'>('create')
const editingId = ref<number | null>(null)
const saving = ref(false)
const formIdempotencyKey = ref('')
const formState = reactive<Record<keyof typeof initialForm, string>>({ ...initialForm })
const detail = ref<Row | null>(null)

const shiftRows = computed(() => summary.value.shifts ?? [])
const summaryCards = computed(() => [
  { label: '已完成服务', value: summary.value.completed_count },
  { label: '已取消服务（不计入）', value: summary.value.cancelled_count },
  { label: '加注趟数 / 加注量', value: `${summary.value.water_fill_count} 趟 / ${summary.value.water_amount} 升` },
  { label: '排污趟数 / 排污量', value: `${summary.value.waste_discharge_count} 趟 / ${summary.value.waste_amount} 升` },
])

function availableActions(row: Row): string[] {
  switch (row['服务状态']) {
    case '待服务':
      return ['安排服务', '完成服务', '取消服务']
    case '服务中':
      return ['完成服务', '取消服务']
    case '已完成':
      return ['取消服务']
    default:
      return []
  }
}

function resetFilters() {
  keyword.value = ''
  statusFilter.value = ''
  summaryDate.value = ''
  void reload()
}

function exportRows() {
  window.open(`${ENDPOINT}/export`, '_blank')
}

function openCreate() {
  formMode.value = 'create'
  editingId.value = null
  Object.assign(formState, initialForm)
  formIdempotencyKey.value = crypto.randomUUID()
  formOpen.value = true
}

function openEdit(row: Row) {
  formMode.value = 'edit'
  editingId.value = Number(row.id)
  Object.assign(formState, {
    排污编号: String(row['排污编号'] ?? ''),
    对应航班: String(row['对应航班'] ?? ''),
    清水加注量: String(row['清水加注量'] ?? ''),
    排污量: String(row['排污量'] ?? ''),
    服务车辆: String(row['服务车辆'] ?? ''),
    操作人员: String(row['操作人员'] ?? ''),
    完成时间: toDatetimeLocal(row['完成时间']),
    班次: String(row['班次'] ?? ''),
  })
  formIdempotencyKey.value = crypto.randomUUID()
  formOpen.value = true
}

function closeForm() {
  formOpen.value = false
  saving.value = false
}

async function openDetail(row: Row) {
  errorMessage.value = ''
  try {
    const response = await request(`${ENDPOINT}/${row.id}`)
    if (!response.ok) {
      throw new Error(await readServerError(response, '排污任务详情读取失败'))
    }
    detail.value = await response.json()
  } catch (error) {
    errorMessage.value = error instanceof Error ? error.message : '排污任务详情读取失败'
  }
}

async function saveForm() {
  errorMessage.value = ''
  successMessage.value = ''
  saving.value = true
  try {
    const values = Object.fromEntries(
      Object.entries(formState).map(([key, value]) => [key, key.includes('量') ? normalizeNumber(value) : value]),
    )
    const isCreate = formMode.value === 'create'
    const response = await request(
      isCreate ? ENDPOINT : `${ENDPOINT}/${editingId.value}`,
      {
        method: isCreate ? 'POST' : 'PATCH',
        headers: isCreate ? { 'Idempotency-Key': formIdempotencyKey.value } : undefined,
        body: JSON.stringify({ values }),
      },
    )
    const payload = await response.json().catch(() => null)
    if (!response.ok || payload?.ok === false) {
      throw new Error(readPayloadMessage(payload, response, '排污任务保存失败'))
    }
    successMessage.value = payload?.message ?? '排污任务已保存'
    formOpen.value = false
    await reload()
  } catch (error) {
    errorMessage.value = error instanceof Error ? error.message : '排污任务保存失败'
  } finally {
    saving.value = false
  }
}

async function runAction(action: string, row: Row) {
  errorMessage.value = ''
  successMessage.value = ''
  busyId.value = Number(row.id)
  try {
    const response = await request(`${ENDPOINT}/${row.id}/actions`, {
      method: 'POST',
      body: JSON.stringify({ values: { action } }),
    })
    const payload = await response.json().catch(() => null)
    if (!response.ok || payload?.ok === false) {
      throw new Error(readPayloadMessage(payload, response, '清水排污动作未生效'))
    }
    successMessage.value = payload?.message ?? '清水排污动作已生效'
    await reload()
  } catch (error) {
    errorMessage.value = error instanceof Error ? error.message : '清水排污操作失败'
  } finally {
    busyId.value = null
  }
}

async function recalculateShifts() {
  errorMessage.value = ''
  successMessage.value = ''
  recalculating.value = true
  try {
    const response = await request('/api/lavatory/shifts/rebuild', { method: 'POST' })
    const payload = await response.json().catch(() => null)
    if (!response.ok || payload?.ok === false) {
      throw new Error(readPayloadMessage(payload, response, '历史班次重算失败'))
    }
    summary.value = payload
    successMessage.value = payload?.message ?? '历史班次已按当前明细重算'
    await loadEntries()
  } catch (error) {
    errorMessage.value = error instanceof Error ? error.message : '历史班次重算失败'
  } finally {
    recalculating.value = false
  }
}

async function reload() {
  await Promise.all([loadSummary(), loadEntries()])
}

async function loadSummary() {
  errorMessage.value = ''
  const query = new URLSearchParams()
  if (summaryDate.value) query.set('shift_date', summaryDate.value)
  try {
    const response = await request(`${ENDPOINT}/summary${query.size ? `?${query}` : ''}`)
    if (!response.ok) {
      throw new Error(await readServerError(response, '班次汇总读取失败'))
    }
    summary.value = await response.json()
  } catch (error) {
    errorMessage.value = error instanceof Error ? error.message : '班次汇总读取失败'
  }
}

async function loadEntries() {
  const query = new URLSearchParams()
  if (keyword.value) query.set('keyword', keyword.value)
  if (statusFilter.value) query.set('status', statusFilter.value)
  try {
    const response = await request(`${ENDPOINT}${query.size ? `?${query}` : ''}`)
    if (!response.ok) {
      throw new Error(await readServerError(response, '排污任务列表读取失败'))
    }
    const payload = await response.json()
    rows.value = payload.items ?? []
    total.value = payload.total ?? rows.value.length
  } catch (error) {
    errorMessage.value = error instanceof Error ? error.message : '清水排污列表读取失败'
  }
}

function normalizeNumber(value: string): string {
  return value.trim() === '' ? '' : value
}

function toDatetimeLocal(value: string | number | boolean | null): string {
  const text = String(value ?? '')
  return text.replace(' ', 'T').slice(0, 16)
}

async function readServerError(response: Response, fallback: string): Promise<string> {
  const payload = await response.json().catch(() => null)
  return readPayloadMessage(payload, response, fallback)
}

function formatValidationDetail(item: unknown): string {
  if (!item || typeof item !== 'object') return ''
  const detail = item as Record<string, unknown>
  if (typeof detail.message === 'string') {
    const location = Array.isArray(detail.loc) && detail.loc.length ? String(detail.loc[detail.loc.length - 1]) : '请求参数'
    return `${location}：${detail.message}`
  }
  return ''
}

function readPayloadMessage(payload: unknown, response: Response, fallback: string): string {
  if (payload && typeof payload === 'object') {
    const data = payload as Record<string, unknown>
    if (typeof data.message === 'string' && data.message) return data.message
    if (typeof data.detail === 'string' && data.detail) return data.detail
    if (Array.isArray(data.detail)) {
      return data.detail.map((item: unknown) => formatValidationDetail(item)).filter(Boolean).join('；') || fallback
    }
    if (data.detail && typeof data.detail === 'object') {
      const detail = data.detail as Record<string, unknown>
      if (typeof detail.message === 'string' && detail.message) return detail.message
    }
  }
  return `${fallback}（HTTP ${response.status}）`
}

onMounted(reload)
</script>
