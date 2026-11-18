<template>
  <section class="page" data-module="column_mapping_version">
    <header class="page-head">
      <div>
        <h2>字段映射版本表</h2>
        <p class="page-desc">
          解析源字段映射规则生成版本表，保留源字段、目标字段与生效状态；版本按顺序推进不跳级，
          生效与明细刷新标记原子落库，沿数据流定位字段映射结果。
        </p>
      </div>
      <div class="page-actions">
        <RouterLink class="btn" data-testid="back-to-mapping" to="/column_mapping">返回字段映射列表核对</RouterLink>
        <button class="btn primary" data-testid="open-parse" type="button" @click="openParse">解析映射规则</button>
        <button class="btn" data-testid="export" type="button" @click="exportRows">导出版本表清单</button>
      </div>
    </header>

    <div class="stat-row">
      <article v-for="item in stats" :key="item.label" class="stat-card">
        <span class="stat-label">{{ item.label }}</span>
        <strong class="stat-value">{{ item.value }}</strong>
      </article>
    </div>

    <nav class="tab-bar" data-testid="tab-bar">
      <button
        v-for="tab in tabs"
        :key="tab.key"
        type="button"
        class="tab-item"
        :class="{ active: activeTab === tab.key }"
        :data-testid="`tab-${tab.key}`"
        @click="switchTab(tab.key)"
      >
        {{ tab.label }}
      </button>
    </nav>

    <!-- 版本列表：筛选、检索、过滤、排序、翻页、定位 -->
    <template v-if="activeTab === 'versions'">
      <form class="filter-bar" @submit.prevent="reload">
        <label v-for="field in filterFields" :key="field" class="filter-item">
          <span>{{ field }}</span>
          <input v-model="filters[field]" :data-testid="`filter-${field}`" :placeholder="`按${field}检索`" />
        </label>
        <label class="filter-item">
          <span>生效状态</span>
          <select v-model="statusFilter" data-testid="filter-status">
            <option value="">全部</option>
            <option v-for="s in statuses" :key="s" :value="s">{{ s }}</option>
          </select>
        </label>
        <label class="filter-item">
          <span>关键字</span>
          <input v-model="keyword" data-testid="search-keyword" placeholder="规则编号/源字段/目标字段" />
        </label>
        <button class="btn" data-testid="query" type="submit">查询</button>
        <button class="btn ghost" data-testid="reset" type="button" @click="resetFilters">重置条件</button>
      </form>

      <table class="data-table">
        <thead>
          <tr>
            <th v-for="column in sortableColumns" :key="column.key" class="sortable-th">
              <button type="button" class="sort-link" :data-testid="`sort-${column.key}`" @click="toggleSort(column.key)">
                {{ column.label }}
                <span v-if="sortBy === column.key">{{ sortOrder === 'asc' ? '▲' : '▼' }}</span>
              </button>
            </th>
            <th v-for="column in plainColumns" :key="column">{{ column }}</th>
            <th>生效状态</th>
            <th>冲突</th>
            <th>可执行动作</th>
          </tr>
        </thead>
        <tbody>
          <tr v-for="row in rows" :key="String(row.id)" :data-testid="`row-${row.id}`" :data-status="String(row.status)">
            <td v-for="column in sortableColumns" :key="column.key">{{ row[column.key] ?? '—' }}</td>
            <td v-for="column in plainColumns" :key="column">{{ row[column] ?? '—' }}</td>
            <td>{{ row.status }}</td>
            <td>
              <span v-if="row.冲突标记" class="badge conflict" :title="String(row.冲突说明 ?? '')">冲突待定</span>
              <span v-else>—</span>
            </td>
            <td class="row-actions">
              <button
                v-for="action in availableActions(row.status)"
                :key="action"
                class="link"
                type="button"
                :data-testid="`action-${row.id}-${action}`"
                @click="runAction(action, row)"
              >
                {{ action }}
              </button>
            </td>
          </tr>
          <tr v-if="!rows.length">
            <td :colspan="sortableColumns.length + plainColumns.length + 3" class="empty-state">
              暂无字段映射版本数据，可先解析映射规则
            </td>
          </tr>
        </tbody>
      </table>

      <footer class="page-foot pager">
        <button class="btn" data-testid="prev-page" type="button" :disabled="page <= 1" @click="goPage(page - 1)">上一页</button>
        <span data-testid="page-info">第 {{ page }} 页 / 共 {{ totalPages }} 页，共 {{ total }} 条</span>
        <button class="btn" data-testid="next-page" type="button" :disabled="page >= totalPages" @click="goPage(page + 1)">下一页</button>
        <label class="filter-item">
          <span>每页</span>
          <select v-model.number="size" data-testid="page-size" @change="reload">
            <option :value="5">5</option>
            <option :value="10">10</option>
            <option :value="20">20</option>
          </select>
        </label>
        <label class="filter-item">
          <span>定位到版本</span>
          <input v-model="locateId" data-testid="locate-id" placeholder="版本行 id" />
        </label>
        <button class="btn ghost" data-testid="locate" type="button" @click="locateRow">定位</button>
      </footer>
    </template>

    <!-- 当前字段列表：查询映射后只出现一份当前生效版本，两条排序边界 -->
    <template v-else-if="activeTab === 'current'">
      <div class="filter-bar">
        <span class="filter-item"><span>排序边界</span></span>
        <button
          type="button"
          class="btn"
          :class="{ primary: currentSort === '生效日期' }"
          data-testid="current-sort-date"
          @click="loadCurrent('生效日期')"
        >
          按生效日期排序
        </button>
        <button
          type="button"
          class="btn"
          :class="{ primary: currentSort === '登记顺序' }"
          data-testid="current-sort-seq"
          @click="loadCurrent('登记顺序')"
        >
          按登记顺序排序
        </button>
        <span class="page-desc">仅显示已生效版本；同一目标字段存在多条时保留全部当前版本，优先级冲突不裁决。</span>
      </div>
      <table class="data-table">
        <thead>
          <tr>
            <th v-for="column in currentColumns" :key="column">{{ column }}</th>
          </tr>
        </thead>
        <tbody>
          <tr v-for="row in currentRows" :key="String(row.id)" :data-testid="`current-row-${row.id}`">
            <td v-for="column in currentColumns" :key="column">{{ row[column] ?? '—' }}</td>
          </tr>
          <tr v-if="!currentRows.length">
            <td :colspan="currentColumns.length" class="empty-state">暂无已生效版本</td>
          </tr>
        </tbody>
      </table>
    </template>

    <!-- 冲突清单：两个已生效版本写同一目标字段，比较处理先后待定 -->
    <template v-else-if="activeTab === 'conflicts'">
      <table class="data-table">
        <thead>
          <tr>
            <th v-for="column in conflictColumns" :key="column">{{ column }}</th>
          </tr>
        </thead>
        <tbody>
          <tr v-for="row in conflictRows" :key="String(row.id)" :data-testid="`conflict-row-${row.id}`">
            <td v-for="column in conflictColumns" :key="column">{{ row[column] ?? '—' }}</td>
          </tr>
          <tr v-if="!conflictRows.length">
            <td :colspan="conflictColumns.length" class="empty-state">当前无目标字段冲突</td>
          </tr>
        </tbody>
      </table>
      <footer class="page-foot">字段映射谁优先尚无结论，冲突仅标记不裁决。</footer>
    </template>

    <!-- 明细刷新标记：刷新原因定位到规则编号 -->
    <template v-else>
      <form class="filter-bar" @submit.prevent="loadRefreshMarks">
        <label class="filter-item">
          <span>规则编号</span>
          <input v-model="markRuleNo" data-testid="mark-filter-rule" placeholder="按规则编号定位刷新原因" />
        </label>
        <label class="filter-item">
          <span>明细表名称</span>
          <input v-model="markTable" data-testid="mark-filter-table" placeholder="按明细表过滤" />
        </label>
        <button class="btn" data-testid="mark-query" type="submit">查询</button>
        <button class="btn ghost" type="button" @click="resetMarkFilters">重置</button>
      </form>
      <table class="data-table">
        <thead>
          <tr>
            <th v-for="column in markColumns" :key="column">{{ column }}</th>
          </tr>
        </thead>
        <tbody>
          <tr v-for="row in markRows" :key="String(row.id)" :data-testid="`mark-row-${row.id}`">
            <td v-for="column in markColumns" :key="column">{{ row[column] ?? '—' }}</td>
          </tr>
          <tr v-if="!markRows.length">
            <td :colspan="markColumns.length" class="empty-state">暂无明细刷新标记</td>
          </tr>
        </tbody>
      </table>
    </template>

    <div v-if="showParse" class="modal-backdrop" data-testid="parse-modal">
      <form class="modal" @submit.prevent="submitParse">
        <h3>解析映射规则生成版本</h3>
        <label v-for="field in parseFields" :key="field" class="create-field">
          <span>{{ field }}</span>
          <input v-model="parseValues[field]" :data-testid="`parse-${field}`" :required="field !== '数据类型'" />
        </label>
        <p class="page-desc">源字段名与目标字段名按逗号对齐，非法值按功能优化失败处理，不会产生半截版本。</p>
        <div class="page-actions">
          <button class="btn" type="button" @click="showParse = false">取消</button>
          <button class="btn primary" data-testid="submit-parse" type="submit" :disabled="parsing">
            {{ parsing ? '解析中…' : '确认解析' }}
          </button>
        </div>
      </form>
    </div>

    <footer class="page-foot">
      <span>共 {{ total }} 条字段映射版本记录</span>
      <span v-if="errorMessage" class="error-text">{{ errorMessage }}</span>
    </footer>
  </section>
</template>

<script setup lang="ts">
import { computed, onMounted, ref } from 'vue'

import { request } from '@/api/client'

type Row = Record<string, string | number | boolean | null>

const ENDPOINT = '/api/column_mapping_version'
const statuses = ['草稿', '待校验', '已生效', '已废弃']
const tabs = [
  { key: 'versions', label: '版本列表' },
  { key: 'current', label: '当前字段列表' },
  { key: 'conflicts', label: '冲突清单' },
  { key: 'marks', label: '明细刷新标记' },
] as const

type TabKey = (typeof tabs)[number]['key']
const activeTab = ref<TabKey>('versions')

const sortableColumns = [
  { key: '版本编号', label: '版本编号' },
  { key: '规则编号', label: '规则编号' },
  { key: '登记顺序', label: '登记顺序' },
  { key: '生效日期', label: '生效日期' },
]
const plainColumns = ['源字段', '目标字段', '数据类型']
const filterFields = ['规则编号', '源字段', '目标字段']
const currentColumns = ['版本编号', '规则编号', '源字段', '目标字段', '登记顺序', '生效日期']
const conflictColumns = ['版本编号', '规则编号', '源字段', '目标字段', '生效日期', '冲突说明']
const markColumns = ['规则编号', '版本编号', '源字段', '目标字段', '明细表名称', '刷新原因', '刷新水位', '刷新状态']
const parseFields = ['规则编号', '源字段名', '目标字段名', '数据类型', '生效日期', '来源批次']

const rows = ref<Row[]>([])
const total = ref(0)
const page = ref(1)
const size = ref(10)
const sortBy = ref('登记顺序')
const sortOrder = ref<'asc' | 'desc'>('asc')
const filters = ref<Record<string, string>>({})
const statusFilter = ref('')
const keyword = ref('')
const locateId = ref('')
const errorMessage = ref('')
const showParse = ref(false)
const parsing = ref(false)
const parseValues = ref<Record<string, string>>({})

const currentRows = ref<Row[]>([])
const currentSort = ref('生效日期')
const conflictRows = ref<Row[]>([])
const markRows = ref<Row[]>([])
const markRuleNo = ref('')
const markTable = ref('')

const totalPages = computed(() => Math.max(1, Math.ceil(total.value / size.value)))
const stats = computed(() => [
  { label: '版本记录总数', value: total.value },
  { label: '当前页已生效', value: rows.value.filter((row) => row.status === '已生效').length },
  { label: '冲突版本', value: conflictRows.value.length },
  { label: '刷新标记', value: markRows.value.length },
])

function availableActions(status: string | number | boolean | null | undefined): string[] {
  const idx = statuses.indexOf(String(status))
  if (idx < 0 || idx >= statuses.length - 1) return []
  const nextAction = ['提交校验', '标记生效', '标记废弃']
  return [nextAction[idx]]
}

function switchTab(key: TabKey) {
  activeTab.value = key
  errorMessage.value = ''
  if (key === 'current') void loadCurrent(currentSort.value)
  if (key === 'conflicts') void loadConflicts()
  if (key === 'marks') void loadRefreshMarks()
}

function resetFilters() {
  filters.value = {}
  statusFilter.value = ''
  keyword.value = ''
  page.value = 1
  void reload()
}

function toggleSort(column: string) {
  if (sortBy.value === column) {
    sortOrder.value = sortOrder.value === 'asc' ? 'desc' : 'asc'
  } else {
    sortBy.value = column
    sortOrder.value = 'asc'
  }
  page.value = 1
  void reload()
}

function goPage(target: number) {
  page.value = Math.min(Math.max(1, target), totalPages.value)
  void reload()
}

async function locateRow() {
  const id = Number(locateId.value.trim())
  if (!id) {
    errorMessage.value = '请输入要定位的版本行 id'
    return
  }
  errorMessage.value = ''
  try {
    const response = await request(`${ENDPOINT}/${id}`)
    if (!response.ok) throw new Error('定位的版本不存在或已归档')
    const row: Row = await response.json()
    // 定位到该版本所在页并高亮筛选边界
    const seq = Number(row.登记顺序 ?? 0)
    page.value = Math.max(1, Math.ceil(seq / size.value))
    await reload()
    errorMessage.value = `已定位到版本 ${row.版本编号}（第 ${page.value} 页）`
  } catch (error) {
    errorMessage.value = error instanceof Error ? error.message : '版本定位失败'
  }
}

async function reload() {
  errorMessage.value = ''
  const params = new URLSearchParams({
    page: String(page.value),
    size: String(size.value),
    sort_by: sortBy.value,
    sort_order: sortOrder.value,
  })
  for (const [key, value] of Object.entries(filters.value)) {
    if (value.trim()) params.set(key, value.trim())
  }
  if (statusFilter.value) params.set('status', statusFilter.value)
  if (keyword.value.trim()) params.set('keyword', keyword.value.trim())
  try {
    const response = await request(`${ENDPOINT}?${params.toString()}`)
    if (!response.ok) {
      const payload = await response.json().catch(() => ({ detail: '版本表读取失败' }))
      throw new Error(payload.detail || '版本表读取失败')
    }
    const payload = await response.json()
    rows.value = payload.items ?? []
    total.value = payload.total ?? 0
  } catch (error) {
    errorMessage.value = error instanceof Error ? error.message : '字段映射版本表读取失败'
  }
}

async function loadCurrent(sort: string) {
  currentSort.value = sort
  const response = await request(`${ENDPOINT}/current?sort_by=${encodeURIComponent(sort)}`)
  if (response.ok) {
    const payload = await response.json()
    currentRows.value = payload.items ?? []
  }
}

async function loadConflicts() {
  const response = await request(`${ENDPOINT}/conflicts`)
  if (response.ok) {
    const payload = await response.json()
    conflictRows.value = payload.items ?? []
  }
}

async function loadRefreshMarks() {
  errorMessage.value = ''
  const params = new URLSearchParams()
  if (markRuleNo.value.trim()) params.set('rule_no', markRuleNo.value.trim())
  if (markTable.value.trim()) params.set('table_name', markTable.value.trim())
  const response = await request(`${ENDPOINT}/refresh_marks?${params.toString()}`)
  if (response.ok) {
    const payload = await response.json()
    markRows.value = payload.items ?? []
  } else {
    errorMessage.value = '明细刷新标记读取失败'
  }
}

function resetMarkFilters() {
  markRuleNo.value = ''
  markTable.value = ''
  void loadRefreshMarks()
}

function openParse() {
  parseValues.value = Object.fromEntries(parseFields.map((field) => [field, '']))
  errorMessage.value = ''
  showParse.value = true
}

async function submitParse() {
  parsing.value = true
  errorMessage.value = ''
  try {
    const response = await request(`${ENDPOINT}/parse`, {
      method: 'POST',
      body: JSON.stringify(parseValues.value),
    })
    const payload = await response.json()
    if (!response.ok || !payload.ok) throw new Error(payload.message || '解析失败')
    showParse.value = false
    page.value = 1
    await reload()
  } catch (error) {
    errorMessage.value = error instanceof Error ? error.message : '映射规则解析失败'
  } finally {
    parsing.value = false
  }
}

async function runAction(action: string, row: Row) {
  errorMessage.value = ''
  try {
    const response = await request(`${ENDPOINT}/${row.id}/actions`, {
      method: 'POST',
      body: JSON.stringify({ action }),
    })
    const payload = await response.json()
    if (!response.ok || !payload.ok) throw new Error(payload.message || '动作未生效')
    await reload()
    if (activeTab.value === 'conflicts') await loadConflicts()
    if (activeTab.value === 'marks') await loadRefreshMarks()
  } catch (error) {
    errorMessage.value = error instanceof Error ? error.message : '字段映射版本操作失败'
  }
}

async function exportRows() {
  errorMessage.value = ''
  const params = new URLSearchParams({ sort_by: sortBy.value, sort_order: sortOrder.value })
  for (const [key, value] of Object.entries(filters.value)) {
    if (value.trim()) params.set(key, value.trim())
  }
  if (statusFilter.value) params.set('status', statusFilter.value)
  try {
    const response = await request(`${ENDPOINT}/export?${params.toString()}`)
    if (!response.ok) throw new Error('导出失败，请稍后重试')
    const url = URL.createObjectURL(await response.blob())
    const link = document.createElement('a')
    link.href = url
    link.download = 'column_mapping_version.csv'
    link.click()
    URL.revokeObjectURL(url)
  } catch (error) {
    errorMessage.value = error instanceof Error ? error.message : '导出失败'
  }
}

onMounted(() => {
  void reload()
  void loadConflicts()
  void loadRefreshMarks()
})
</script>

<style scoped>
.tab-bar {
  display: flex;
  gap: 8px;
  margin: 12px 0;
  border-bottom: 1px solid var(--border-color, #e2e6ec);
}
.tab-item {
  padding: 8px 16px;
  border: none;
  background: transparent;
  cursor: pointer;
  color: #5b6472;
  border-bottom: 2px solid transparent;
}
.tab-item.active {
  color: #1f6feb;
  border-bottom-color: #1f6feb;
  font-weight: 600;
}
.sortable-th .sort-link {
  border: none;
  background: none;
  padding: 0;
  font: inherit;
  color: inherit;
  cursor: pointer;
}
.pager {
  display: flex;
  align-items: center;
  gap: 12px;
  flex-wrap: wrap;
}
.badge {
  display: inline-block;
  padding: 2px 8px;
  border-radius: 10px;
  font-size: 12px;
}
.badge.conflict {
  background: #fff1f0;
  color: #cf1322;
  border: 1px solid #ffa39e;
}
</style>
