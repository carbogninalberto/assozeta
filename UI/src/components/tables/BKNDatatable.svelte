<script>
	import { ArrowDown, ArrowLeft, ArrowRight, ArrowUp, ChevronsLeft, ChevronsRight, Search } from 'lucide-svelte';
    import {CaretDown, CaretRight, XCircle} from 'phosphor-svelte';
    import {onMount, onDestroy, createEventDispatcher, tick} from 'svelte';
    import {apiFetch} from 'utils/ApiMiddleware';
    import {UiUtil} from 'shim/ui.js';
    import {estimateHeaderMinimumWidth} from './datatableColumnLayout.js';

    const dispatch = createEventDispatcher();
    const responsiveBreakpoints = ['xxxxl', 'xxxl', 'xxl', 'xl', 'lg', 'md', 'sm'];
    const responsiveBreakpointOrder = ['sm', 'md', 'lg', 'xl', 'xxl', 'xxxl', 'xxxxl'];
    const responsiveBreakpointMaxWidths = {sm: 575.98, md: 767.98, lg: 991.98, xl: 1199.98, xxl: 1399.98, xxxl: 1599.98, xxxxl: 1799.98};

    export let id = 'bkn_datatable';
    export let searchId = 'bkn_datatable_search_query';
    export let selectedCounter = 0;
    export let visibleMultiaction = false;
    export let datatable;
    export let url = undefined;
    export let localData = undefined;
    export let params = {};
    export let columns = [];
    export let dataKey = undefined;
    export let responsive = true;
    export let spinnerConfig = undefined;
    export let serverPaging = localData ? false : true;
    export let serverFiltering = localData ? false : true;
    export let serverSorting = localData ? false : true;
    export let pageSize = 10;
    export let pageSizeSelect = [10, 20, 30, 50];
    export let showDividerFilter = true;
    export let showSearch = true;
    export let wrapText = true;
    let searchValue = '';
    export let mapFunction = function (raw) {
        var dataSet = raw;
        if (typeof raw.data !== 'undefined') {
            dataSet = raw.data;
        }
        return dataSet;
    };
    export let clicked = () => {};

    export let loadFilters = function () {};

    let dataSet = [];
    let allRows = [];
    let clientFilters = {};
    let selectedRows = new Set();
    let expandedRows = new Set();
    let loading = false;
    let errorMessage = '';
    let currentPage = 1;
    let totalPages = 1;
    let totalItems = 0;
    let sortField = '';
    let sortDirection = 'asc';
    let searchTimeout = null;
    let viewportWidth = typeof window === 'undefined' ? 0 : window.innerWidth;
    let tableWidth = 0;
    const eventListeners = {};

    $: selectedCounter = selectedRows.size;
    $: visibleMultiaction = selectedRows.size > 0;

    function getQueryKey(key) {
        if (!key) return key;
        const keyValue = String(key);
        if (keyValue === 'query' || keyValue.includes('[')) return keyValue;
        return `query[${keyValue}]`;
    }

    function isPlainObject(value) {
        return value && typeof value === 'object' && !Array.isArray(value);
    }

    function getPathValue(source, path) {
        if (!path) return undefined;
        return String(path)
            .split('.')
            .reduce((value, segment) => {
                if (value === undefined || value === null) return undefined;
                return value[segment];
            }, source);
    }

    function getFieldValue(row, field) {
        const pathValue = getPathValue(row, field);
        return pathValue === undefined ? row?.[field] : pathValue;
    }

    function toRowArray(value) {
        if (Array.isArray(value)) return value;
        if (value && typeof value === 'object') {
            const keys = Object.keys(value);
            if (keys.length > 0 && keys.every(key => /^\d+$/.test(key))) return Object.values(value);
        }
        return null;
    }

    function normalizeParamEntries(source = {}) {
        const entries = [];

        Object.entries(source || {}).forEach(([key, value]) => {
            if (key === 'query' && isPlainObject(value)) {
                Object.entries(value).forEach(([queryKey, queryValue]) => {
                    entries.push([getQueryKey(queryKey), queryValue]);
                });
            } else {
                entries.push([key, value]);
            }
        });

        return entries;
    }

    function setUrlParam(searchParams, key, value) {
        if (value === undefined || value === null) return;
        searchParams.set(key, Array.isArray(value) ? value.join(',') : value);
    }

    function buildReadUrl() {
        const readUrl = new URL(url, window.location.origin);

        normalizeParamEntries(params).forEach(([key, value]) => setUrlParam(readUrl.searchParams, key, value));

        if (serverPaging) {
            setUrlParam(readUrl.searchParams, 'pagination[page]', currentPage);
            setUrlParam(readUrl.searchParams, 'pagination[perpage]', pageSize);
        }

        if (serverSorting && sortField) {
            setUrlParam(readUrl.searchParams, 'sort[field]', sortField);
            setUrlParam(readUrl.searchParams, 'sort[sort]', sortDirection);
        }

        return url?.startsWith('http') ? readUrl.toString() : `${readUrl.pathname}${readUrl.search}${readUrl.hash}`;
    }

    function readPagination(raw) {
        const pagination = raw?.meta?.pagination || raw?.meta || raw?.pagination || {};
        const readTotal = pagination.total_items ?? pagination.totalItems ?? pagination.total ?? raw?.recordsTotal;
        const readPages = pagination.total_pages ?? pagination.totalPages ?? pagination.pages;

        totalItems = readTotal === undefined || readTotal === null ? allRows.length : Number(readTotal) || 0;
        totalPages = readPages === undefined || readPages === null
            ? Math.max(Math.ceil(totalItems / pageSize), 1)
            : Math.max(Number(readPages) || 1, 1);
    }

    function normalizeRows(raw) {
        if (!raw) return [];

        if (dataKey) {
            const keyedRows = toRowArray(getPathValue(raw, dataKey));
            if (keyedRows) return keyedRows;
        }

        const mappedRows = toRowArray(typeof mapFunction === 'function' ? mapFunction(raw) : undefined);
        if (mappedRows) return mappedRows;

        const dataRows = toRowArray(raw?.data);
        if (dataRows) return dataRows;

        const rawRows = toRowArray(raw);
        if (rawRows) return rawRows;

        console.warn('BKNDatatable: Unable to extract data array from response:', raw);
        return [];
    }

    function getQueryParams(source = params) {
        const query = {};

        normalizeParamEntries(source).forEach(([key, value]) => {
            const match = String(key).match(/^query\[(.*)\]$/);
            if (match) query[match[1]] = value;
        });

        return query;
    }

    function getClientFilterKey(key) {
        const match = String(key).match(/^query\[(.*)\]$/);
        return match ? match[1] : key;
    }

    function setQueryParams(query = {}) {
        const nextParams = {};

        Object.entries(params || {}).forEach(([key, value]) => {
            if (key !== 'query' && !String(key).startsWith('query[')) nextParams[key] = value;
        });

        Object.entries(query || {}).forEach(([key, value]) => {
            nextParams[getQueryKey(key)] = value;
        });

        params = nextParams;
    }

    function stringifySearchValue(value) {
        if (value === undefined || value === null) return '';
        if (Array.isArray(value)) return value.map(stringifySearchValue).join(' ');
        if (typeof value === 'object') return Object.values(value).map(stringifySearchValue).join(' ');
        return String(value);
    }

    function getFilterNeedles(value) {
        if (value === undefined || value === null || value === '') return [];
        if (Array.isArray(value)) return value.map(item => String(item).toLowerCase()).filter(Boolean);
        return String(value).split(',').map(item => item.trim().toLowerCase()).filter(Boolean);
    }

    function rowMatchesFilter(row, key, value) {
        const needles = getFilterNeedles(value);
        if (needles.length === 0) return true;

        const haystack = key === 'generalSearch'
            ? stringifySearchValue(row).toLowerCase()
            : stringifySearchValue(getFieldValue(row, key)).toLowerCase();

        return needles.some(needle => haystack.includes(needle));
    }

    function filterClientRows(rows) {
        return rows.filter(row => Object.entries(clientFilters).every(([key, value]) => rowMatchesFilter(row, key, value)));
    }

    function parseDateValue(value) {
        if (!value) return null;
        if (value instanceof Date) return value.getTime();

        const stringValue = String(value).trim();
        const italianDate = stringValue.match(/^(\d{1,2})\/(\d{1,2})\/(\d{4})/);
        const timestamp = italianDate
            ? new Date(Number(italianDate[3]), Number(italianDate[2]) - 1, Number(italianDate[1])).getTime()
            : new Date(stringValue).getTime();

        return Number.isNaN(timestamp) ? null : timestamp;
    }

    function parseNumberValue(value) {
        if (value === undefined || value === null || value === '') return null;
        if (typeof value === 'number') return value;

        const raw = String(value).replace(/[^\d.,\-]/g, '');

        let normalized;
        if (raw.includes('.') && raw.includes(',')) {
            normalized = raw.replace(/\./g, '').replace(',', '.');
        } else if (raw.includes(',')) {
            normalized = raw.replace(',', '.');
        } else {
            normalized = raw;
        }

        const num = parseFloat(normalized);
        return Number.isNaN(num) ? null : num;
    }

    function stripHtml(value) {
        if (value === undefined || value === null) return '';
        return String(value).replace(/<[^>]*>/g, '').trim();
    }

    function compareValues(aValue, bValue, column) {
        if (column.type === 'date') {
            aValue = parseDateValue(aValue);
            bValue = parseDateValue(bValue);
        }

        if (column.type === 'number') {
            aValue = parseNumberValue(aValue);
            bValue = parseNumberValue(bValue);
        }

        if (column.type === 'html') {
            aValue = stripHtml(aValue);
            bValue = stripHtml(bValue);
        }

        const aEmpty = aValue === undefined || aValue === null || aValue === '';
        const bEmpty = bValue === undefined || bValue === null || bValue === '';

        if (aEmpty && bEmpty) return 0;
        if (aEmpty) return 1;
        if (bEmpty) return -1;

        const aNumber = Number(aValue);
        const bNumber = Number(bValue);

        if (!Number.isNaN(aNumber) && !Number.isNaN(bNumber)) return aNumber - bNumber;
        return String(aValue).localeCompare(String(bValue), undefined, {numeric: true, sensitivity: 'base'});
    }

    function sortClientRows(rows) {
        if (!sortField) return rows;

        const column = columns.find(item => item.field === sortField);
        if (!column) return rows;

        if (typeof column.sortCallback === 'function') {
            try {
                const callbackRows = toRowArray(column.sortCallback([...rows], sortDirection, column));
                if (callbackRows) return callbackRows;
            } catch (error) {
                console.error(error);
            }
        }

        return [...rows].sort((a, b) => {
            const result = compareValues(getFieldValue(a, column.field), getFieldValue(b, column.field), column);
            return sortDirection === 'asc' ? result : -result;
        });
    }

    function applyClientRows() {
        let rows = [...allRows];

        if (!serverFiltering) rows = filterClientRows(rows);
        if (!serverSorting) rows = sortClientRows(rows);

        if (!serverPaging || !serverFiltering) {
            totalItems = rows.length;
            totalPages = Math.max(Math.ceil(totalItems / pageSize), 1);
            if (currentPage > totalPages) currentPage = totalPages;
        }

        if (!serverPaging) {
            const start = (currentPage - 1) * pageSize;
            dataSet = rows.slice(start, start + pageSize);
        } else {
            dataSet = rows;
        }
    }

    function clearSelection() {
        selectedRows = new Set();
        expandedRows = new Set();
    }

    async function loadRows() {
        clearSelection();
        errorMessage = '';

        if (localData) {
            allRows = [...localData];
            applyClientRows();
            await tick();
            loadFilters();
            return;
        }

        if (!url) {
            allRows = [];
            dataSet = [];
            totalItems = 0;
            totalPages = 1;
            await tick();
            loadFilters();
            return;
        }

        loading = true;
        try {
            const {response, error} = await apiFetch(buildReadUrl(), {method: 'GET'});
            if (error) throw new Error(response?.message || 'Impossibile caricare i dati.');

            allRows = normalizeRows(response);
            if (serverPaging && serverFiltering) readPagination(response);
            applyClientRows();
        } catch (error) {
            allRows = [];
            dataSet = [];
            totalItems = 0;
            totalPages = 1;
            errorMessage = error?.message || 'Impossibile caricare i dati.';
        } finally {
            loading = false;
            await tick();
            loadFilters();
        }
    }

    function createRecordNode(rowIndex) {
        return {dataset: {row: String(rowIndex)}};
    }

    function toggleRow(rowIndex, checked) {
        const nextSelection = new Set(selectedRows);
        const wasChecked = nextSelection.has(rowIndex);
        if (checked) nextSelection.add(rowIndex);
        else nextSelection.delete(rowIndex);
        selectedRows = nextSelection;

        if (checked && !wasChecked) {
            emitEvent('datatable-on-check', {rowIndex});
        } else if (!checked && wasChecked) {
            emitEvent('datatable-on-uncheck', {rowIndex});
        }
    }

    function toggleAll(checked) {
        const prevSize = selectedRows.size;
        selectedRows = checked ? new Set(dataSet.map((_, index) => index)) : new Set();

        if (checked && prevSize === 0) {
            emitEvent('datatable-on-check', {all: true});
        } else if (!checked && prevSize > 0) {
            emitEvent('datatable-on-uncheck', {all: true});
        }
    }

    function renderCell(column, row) {
        if (column.selector) return '';
        if (typeof column.template === 'function') return column.template(row);
        const value = column.field ? getFieldValue(row, column.field) : '';
        return value === undefined || value === null ? '' : String(value);
    }

    function clampNumber(value, min, max) {
        return Math.min(Math.max(value, min), max);
    }

    function getPixelWidth(width) {
        if (width === undefined || width === null || width === '') return null;
        if (typeof width === 'number') return width > 0 ? width : null;
        if (String(width).includes('%')) return null;

        const value = String(width).trim();
        if (!/^\d+(\.\d+)?(px)?$/.test(value)) return null;

        const parsed = parseFloat(value);
        return Number.isNaN(parsed) || parsed <= 0 ? null : parsed;
    }

    function resolveWidth(width) {
        if (typeof width === 'number') return `${width}px`;
        if (typeof width === 'string') {
            const num = parseFloat(width);
            if (!isNaN(num) && String(num) === width) return `${num}px`;
        }
        return width;
    }

    function resolveMinWidth(width) {
        if (typeof width === 'string' && width.includes('%')) return null;
        return resolveWidth(width);
    }

    function normalizeColumnName(value) {
        return String(value || '').trim().toLowerCase();
    }

    function isActionColumn(column) {
        if (column.selector) return false;

        const field = normalizeColumnName(column.field);
        const title = normalizeColumnName(column.title);
        const explicitAction = column.action || column.actions || column.isAction || column.type === 'actions';
        const actionName = column.sortable === false
            && (['azione', 'azioni', 'action', 'actions'].includes(field) || ['azione', 'azioni', 'action', 'actions'].includes(title));
        const blankAction = !field && !title && typeof column.template === 'function' && column.sortable === false && column.fireClick !== true;

        return explicitAction || actionName || blankAction;
    }

    function getColumnTextAlign(column) {
        if (isActionColumn(column)) return 'right';
        return column.textAlign;
    }

    function shouldWrapColumn(column) {
        if (column.selector || isActionColumn(column) || column.overflow === 'visible') return false;
        if (column.wrap === false || column.noWrap || column.nowrap) return false;
        return wrapText || column.wrap === true;
    }

    function getColumnTextSamples(column, rows = []) {
        const samples = [column.title || column.field || ''];

        if (column.field && typeof column.template !== 'function') {
            rows.slice(0, 25).forEach(row => {
                samples.push(stripHtml(stringifySearchValue(getFieldValue(row, column.field))));
            });
        }

        return samples.map(value => String(value || '').trim()).filter(Boolean);
    }

    function estimateColumnWidth(column, rows = []) {
        if (column.selector) return 40;

        const samples = getColumnTextSamples(column, rows);
        const longestText = samples.reduce((max, value) => Math.max(max, value.length), 0);
        const longestWord = samples.reduce((max, value) => {
            return Math.max(max, ...value.split(/\s+/).map(word => word.length));
        }, 0);
        const usefulLength = shouldWrapColumn(column) ? Math.max(longestWord, Math.min(longestText, 24)) : longestText;
        const maxWidth = shouldWrapColumn(column) ? 240 : 360;

        return clampNumber((usefulLength || 10) * 8 + 40, 80, maxWidth);
    }

    function getConfiguredColumnWidth(column) {
        return getPixelWidth(column.width) || getPixelWidth(column.minWidth);
    }

    function getReadableMinimumWidth(column, configuredWidth = null) {
        if (column.selector) return 40;
        if (isActionColumn(column)) return 80;
        if (!normalizeColumnName(column.title) && typeof column.template === 'function' && configuredWidth && configuredWidth <= 48) return 48;
        return 80;
    }

    function getHeaderMinimumWidth(column) {
        return estimateHeaderMinimumWidth(column.title, {
            selector: Boolean(column.selector),
            action: isActionColumn(column),
        });
    }

    function getColumnPreferredWidth(column, rows = dataSet) {
        const configuredWidth = getConfiguredColumnWidth(column);
        const readableMinimumWidth = getReadableMinimumWidth(column, configuredWidth);
        const headerMinimumWidth = getHeaderMinimumWidth(column);

        if (configuredWidth) return Math.max(configuredWidth, readableMinimumWidth, headerMinimumWidth);
        return Math.max(estimateColumnWidth(column, rows), headerMinimumWidth);
    }

    function getColumnMinimumWidth(column, rows = dataSet) {
        const explicitMinWidth = getPixelWidth(column.minWidth);
        const preferredWidth = getColumnPreferredWidth(column, rows);
        const headerMinimumWidth = getHeaderMinimumWidth(column);

        if (column.selector) return 40;
        if (explicitMinWidth) return Math.max(explicitMinWidth, headerMinimumWidth);
        if (isActionColumn(column)) return preferredWidth;
        if (!shouldWrapColumn(column)) return preferredWidth;

        const shrinkTarget = preferredWidth * 0.55;
        return Math.round(clampNumber(shrinkTarget, Math.max(72, headerMinimumWidth), preferredWidth));
    }

    function isGrowableColumn(column) {
        const configuredWidth = getConfiguredColumnWidth(column);

        if (column.selector || isActionColumn(column) || !shouldWrapColumn(column)) return false;
        if (!normalizeColumnName(column.title) && configuredWidth && configuredWidth <= 80) return false;
        return !configuredWidth || configuredWidth >= 120;
    }

    function getColumnLayoutWidth(column, layoutWidth) {
        return layoutWidth || getColumnPreferredWidth(column);
    }

    function getColumnStyle(column, layoutWidth) {
        const styles = [];
        const minWidth = resolveMinWidth(column.minWidth);
        const columnWidth = getColumnLayoutWidth(column, layoutWidth);
        const textAlign = getColumnTextAlign(column);

        styles.push(`width: ${columnWidth}px`);
        if (minWidth) styles.push(`min-width: ${minWidth}`);
        if (textAlign) styles.push(`text-align: ${textAlign}`);
        return styles.join('; ');
    }

    function getColumnSpanStyle(column, layoutWidth) {
        const styles = [];
        const minWidth = resolveMinWidth(column.minWidth);
        const columnWidth = getColumnLayoutWidth(column, layoutWidth);
        const textAlign = getColumnTextAlign(column);

        styles.push(`width: ${columnWidth}px`);
        if (minWidth) styles.push(`min-width: ${minWidth}`);
        if (column.maxWidth) styles.push(`max-width: ${resolveWidth(column.maxWidth)}`);
        if (textAlign) styles.push(`text-align: ${textAlign}`);
        if (column.overflow) styles.push(`overflow: ${column.overflow}`);
        return styles.join('; ');
    }

    function getColumnMinContribution(column, rows = []) {
        if (column.selector) return 40;
        return getColumnMinimumWidth(column, rows);
    }

    function computeTableLayout(cols, withDetailToggle, width, rows, availableWidth) {
        const visibleColumns = (cols || []).filter(column => !isColumnHiddenAtViewport(column, width));
        const visibleColumnSet = new Set(visibleColumns);
        const detailToggleWidth = withDetailToggle ? 24 : 0;
        const preferredWidths = (cols || []).map(column => getColumnPreferredWidth(column, rows));
        const minimumWidths = (cols || []).map(column => getColumnMinContribution(column, rows));
        const preferredTotal = visibleColumns.reduce((sum, column) => sum + getColumnPreferredWidth(column, rows), detailToggleWidth);
        const minimumTotal = visibleColumns.reduce((sum, column) => sum + getColumnMinContribution(column, rows), detailToggleWidth);
        const usableWidth = availableWidth > 0 ? Math.floor(availableWidth) : 0;
        const columnWidths = [...preferredWidths];

        if (usableWidth > 0 && preferredTotal > usableWidth) {
            if (minimumTotal >= usableWidth) {
                (cols || []).forEach((column, index) => {
                    if (visibleColumnSet.has(column)) columnWidths[index] = minimumWidths[index];
                });

                return {columnWidths, rowMinWidth: Math.ceil(minimumTotal)};
            }

            const reductionNeeded = preferredTotal - usableWidth;
            const shrinkCapacity = visibleColumns.reduce((sum, column) => {
                return sum + Math.max(getColumnPreferredWidth(column, rows) - getColumnMinContribution(column, rows), 0);
            }, 0);

            if (shrinkCapacity > 0) {
                (cols || []).forEach((column, index) => {
                    if (!visibleColumnSet.has(column)) return;

                    const preferredWidth = preferredWidths[index];
                    const minimumWidth = minimumWidths[index];
                    const capacity = Math.max(preferredWidth - minimumWidth, 0);
                    const reduction = reductionNeeded * (capacity / shrinkCapacity);
                    columnWidths[index] = Math.floor(Math.max(preferredWidth - reduction, minimumWidth));
                });
            }

            const fittedTotal = visibleColumns.reduce((sum, column) => sum + columnWidths[(cols || []).indexOf(column)], detailToggleWidth);
            return {columnWidths, rowMinWidth: Math.ceil(Math.min(fittedTotal, usableWidth))};
        }

        if (usableWidth > 0 && preferredTotal < usableWidth) {
            const growableColumns = visibleColumns.filter(isGrowableColumn);
            const growableTotal = growableColumns.reduce((sum, column) => sum + getColumnPreferredWidth(column, rows), 0);
            let distributedWidth = 0;

            if (growableColumns.length > 0 && growableTotal > 0) {
                const remainingWidth = usableWidth - preferredTotal;

                (cols || []).forEach((column, index) => {
                    if (!visibleColumnSet.has(column) || !isGrowableColumn(column)) return;

                    const extraWidth = Math.floor(remainingWidth * (preferredWidths[index] / growableTotal));
                    columnWidths[index] += extraWidth;
                    distributedWidth += extraWidth;
                });

                const firstGrowableColumn = growableColumns[0];
                const firstGrowableIndex = (cols || []).indexOf(firstGrowableColumn);
                columnWidths[firstGrowableIndex] += remainingWidth - distributedWidth;

                return {columnWidths, rowMinWidth: usableWidth};
            }
        }

        return {columnWidths, rowMinWidth: Math.ceil(preferredTotal)};
    }

    function getRowStyle(minWidth) {
        return `width: max(100%, ${minWidth}px)`;
    }

    function getResponsiveBreakpoint(column, isResponsive = responsive) {
        if (!isResponsive || column.autoHide === false) return null;
        const breakpointSource = column.responsive?.visible || column.responsive?.hidden;
        if (!breakpointSource) return null;
        const bp = String(breakpointSource).toLowerCase();
        return responsiveBreakpoints.find(item => bp.includes(item)) || null;
    }

    function isColumnHiddenAtViewport(column, width) {
        const breakpoint = getResponsiveBreakpoint(column);
        if (!breakpoint || !width) return false;
        return width <= responsiveBreakpointMaxWidths[breakpoint];
    }

    function getDetailVisibilityClass(column) {
        return `datatable-detail-visible-below-${getResponsiveBreakpoint(column)}`;
    }

    function getResponsiveDetailColumns(cols, isResponsive) {
        return (cols || []).filter(column => !column.selector && getResponsiveBreakpoint(column, isResponsive));
    }

    function getDetailToggleBreakpoint(detailColumns) {
        return detailColumns.reduce((highest, column) => {
            const bp = getResponsiveBreakpoint(column);
            if (!highest) return bp;
            return responsiveBreakpointOrder.indexOf(bp) > responsiveBreakpointOrder.indexOf(highest) ? bp : highest;
        }, null);
    }

    function toggleDetails(rowIndex) {
        const nextExpandedRows = new Set(expandedRows);
        if (nextExpandedRows.has(rowIndex)) nextExpandedRows.delete(rowIndex);
        else nextExpandedRows.add(rowIndex);
        expandedRows = nextExpandedRows;
    }

    function getCellClass(column, header = false) {
        const classes = ['datatable-cell'];
        const align = getColumnTextAlign(column) || (column.selector ? 'center' : 'left');

        if (column.selector) classes.push('datatable-cell-check');
        if (isActionColumn(column)) classes.push('datatable-cell-actions');
        if (align === 'right') classes.push('datatable-cell-right');
        else if (align === 'center') classes.push('datatable-cell-center');
        else classes.push('datatable-cell-left');

        if (header && column.sortable !== false && column.field && !column.selector) {
            classes.push('datatable-cell-sort');
        }
        if (header && column.field && column.sortable !== false && sortField === column.field) classes.push('datatable-cell-sorted');

        if (!column.selector) {
            if (shouldWrapColumn(column)) classes.push('datatable-cell-wrap');
            else classes.push('datatable-cell-nowrap');
        }
        if (column.overflow === 'visible') classes.push('datatable-cell-overflow-visible');

        const responsiveBreakpoint = getResponsiveBreakpoint(column);
        if (responsiveBreakpoint) {
            classes.push(`datatable-cell-hidden-below-${responsiveBreakpoint}`);
        }

        return classes.join(' ');
    }

    function setPage(page) {
        const nextPage = Math.min(Math.max(page, 1), totalPages);
        if (nextPage === currentPage) return;
        currentPage = nextPage;
        if (!serverPaging) {
            clearSelection();
            applyClientRows();
            return;
        }
        loadRows();
    }

    function changePageSize(value) {
        pageSize = Number(value) || pageSize;
        currentPage = 1;
        if (!serverPaging) {
            clearSelection();
            applyClientRows();
            return;
        }
        loadRows();
    }

    function getVisiblePages() {
        const pages = [];
        const first = Math.max(Math.min(currentPage - 2, totalPages - 4), 1);
        const last = Math.min(first + 4, totalPages);
        for (let page = first; page <= last; page += 1) pages.push(page);
        return pages;
    }

    function handleSort(column) {
        if (column.sortable === false || column.selector || !column.field) return;
        if (sortField === column.field) {
            if (sortDirection === 'desc') {
                sortField = '';
                sortDirection = 'asc';
            } else {
                sortDirection = 'desc';
            }
        } else {
            sortField = column.field;
            sortDirection = 'asc';
        }
        currentPage = 1;
        if (!serverSorting) {
            clearSelection();
            applyClientRows();
            return;
        }
        loadRows();
    }

    function isInteractiveRowTarget(event) {
        return event.target.closest(
            'button, a, input, select, textarea, .checkbox, .datatable-cell-check, .datatable-toggle-detail-button, .action-column, .info-column'
        );
    }

    function handleRowClick(event, row) {
        if (isInteractiveRowTarget(event)) return;

        const cell = event.target.closest('.datatable-cell');
        if (cell) {
            const field = cell.dataset.field;
            const column = columns.find(c => c.field === field);
            if (column && (column.fireClick === false || column.selector)) return;
        }

        clicked(event.currentTarget, row);
    }

    function debouncedSearch(value, key = 'generalSearch') {
        clearTimeout(searchTimeout);
        searchTimeout = setTimeout(() => {
            datatable?.search(value, key);
        }, 300);
    }

    function emitEvent(event, data) {
        dispatch(event, data);
        if (eventListeners[event]) {
            eventListeners[event].forEach(fn => {
                try { fn(data); } catch (e) { console.error(e); }
            });
        }
    }

    function getSpinnerMessage() {
        const message = spinnerConfig?.message;
        if (message === undefined || message === true) return 'Caricamento...';
        if (message === false || message === '') return '';
        return String(message);
    }

    function getLoaderStyle() {
        if (spinnerConfig?.opacity === undefined) return '';
        return `background-color: rgba(255, 255, 255, ${spinnerConfig.opacity});`;
    }

    $: pageStart = totalItems === 0 ? 0 : (currentPage - 1) * pageSize + 1;
    $: pageEnd = Math.min(currentPage * pageSize, totalItems);
    $: spinnerMessage = getSpinnerMessage();
    $: spinnerState = spinnerConfig?.state || 'primary';
    $: loaderStyle = getLoaderStyle();
    $: responsiveDetailColumns = getResponsiveDetailColumns(columns, responsive);
    $: detailToggleBreakpoint = getDetailToggleBreakpoint(responsiveDetailColumns);
    $: hasResponsiveDetails = responsiveDetailColumns.length > 0 && detailToggleBreakpoint;
    $: tableLayout = computeTableLayout(columns, hasResponsiveDetails, viewportWidth, dataSet, tableWidth);
    $: columnWidths = tableLayout.columnWidths;
    $: rowMinWidth = tableLayout.rowMinWidth;

    let rerenderKey = 0;
    let previousViewportBreakpoint = null;

    function getViewportBreakpoint(width) {
        if (!width) return null;
        for (const bp of responsiveBreakpointOrder) {
            if (width <= responsiveBreakpointMaxWidths[bp]) return bp;
        }
        return null;
    }

    $: {
        const currentBreakpoint = getViewportBreakpoint(viewportWidth);
        if (previousViewportBreakpoint !== null && previousViewportBreakpoint !== currentBreakpoint) {
            rerenderKey += 1;
        }
        previousViewportBreakpoint = currentBreakpoint;
    }

    function createDatatableController() {
        return {
            get dataSet() {
                return dataSet;
            },
            source: {
                setLocalData(data) {
                    localData = data || [];
                    loadRows();
                },
            },
            destroy() {
                Object.keys(eventListeners).forEach(key => delete eventListeners[key]);
                clearTimeout(searchTimeout);
            },
            reload() {
                loadRows();
                dispatch('reload');
            },
            search(value, key = 'generalSearch') {
                if (serverFiltering) {
                    params = {...params, [getQueryKey(key)]: value};
                    currentPage = 1;
                    loadRows();
                } else {
                    clientFilters = {...clientFilters, [getClientFilterKey(key)]: value};
                    currentPage = 1;
                    clearSelection();
                    applyClientRows();
                }
                dispatch('search', {value, key});
            },
            getDataSourceParam(key) {
                if (key === 'sort') return sortField ? {field: sortField, sort: sortDirection} : null;
                if (key === 'query') return {...getQueryParams(), ...clientFilters};
                if (key === 'pagination') return {page: currentPage, perpage: pageSize};
                if (Object.prototype.hasOwnProperty.call(params || {}, key)) return params[key];

                const query = getQueryParams();
                const queryKey = getClientFilterKey(key);
                if (Object.prototype.hasOwnProperty.call(query, queryKey)) return query[queryKey];
                if (Object.prototype.hasOwnProperty.call(clientFilters, queryKey)) return clientFilters[queryKey];

                return undefined;
            },
            getDataSourceQuery() {
                return {...getQueryParams(), ...clientFilters};
            },
            setDataSourceQuery(query) {
                if (serverFiltering) {
                    setQueryParams(query);
                    currentPage = 1;
                    loadRows();
                } else {
                    clientFilters = {...(query || {})};
                    currentPage = 1;
                    clearSelection();
                    applyClientRows();
                }
            },
            setDataSourceParams(nextParams) {
                params = nextParams || {};
                currentPage = 1;
                loadRows();
            },
            getSelectedRecords() {
                return Array.from(selectedRows).map(createRecordNode);
            },
            on(event, fn) {
                if (!eventListeners[event]) eventListeners[event] = [];
                eventListeners[event].push(fn);
            },
            rows() {
                return {
                    nodes() {
                        return dataSet.map((_, index) => createRecordNode(index));
                    },
                };
            },
        };
    }

    export let BKNDatatable = (function () {
        var loadDatatable = function () {
            const defaultSortColumn = columns.find(
                column => column.field && (column.sortable === 'asc' || column.sortable === 'desc')
            );
            if (defaultSortColumn && !sortField) {
                sortField = defaultSortColumn.field;
                sortDirection = defaultSortColumn.sortable;
            }
            datatable = createDatatableController();
            loadRows();
            dispatch('ready', datatable);
        };

        return {
            init: function () {
                loadDatatable();
            },
            reload: function () {
                if (datatable) {
                    datatable.reload();
                }
            },
            setData: function (data) {
                if (datatable && data) {
                    datatable.source.setLocalData(data);
                    datatable.reload();
                }
            },
        };
    })();

    export function isMobile() {
        return UiUtil.isMobileDevice();
    }

    onMount(() => {
        BKNDatatable.init();
    });

    onDestroy(() => {
        if (datatable) {
            try { datatable.destroy(); } catch (e) {}
            datatable = undefined;
        }
    });
</script>

<svelte:window bind:innerWidth={viewportWidth} />

<div class="mb-2">
    <div class="row align-items-center mx-0">
        <slot name="filter-bar" />
        <div class="col-12 pb-2">
            <div class="row align-items-center justify-content-left d-flex w-100 flex-wrap gap-2">
                {#if showSearch}
                    <div class="my-1 my-md-0 mr-2 d-flex">
                        <div class="input-icon d-flex">
                            <input
                                type="text"
                                class="form-control form-control-solid mb-0 {searchValue !== ''
                                    ? 'border border-secondary border-2 bg-light'
                                    : 'border border-secondary border-dashed bg-white'}"
                                style="max-width: 28rem;width: 28rem"
                                placeholder="Cerca..."
                                id={searchId}
                                on:input={e => (searchValue = e.target.value)}
                                on:keyup={() => debouncedSearch(searchValue)} />
                            <span>
                                <Search size={16} class="text-muted" />
                            </span>
                            <button
                                style="position: absolute;right:0;"
                                class="btn btn-icon btn-ghost mb-0"
                                class:d-none={searchValue === ''}
                                on:click={() => {
                                    searchValue = '';
                                    document.getElementById(searchId).value = '';
                                    setTimeout(() => {
                                        document.getElementById(searchId).dispatchEvent(new Event('keyup'));
                                    }, 200);
                                }}>
                                <XCircle size={19} weight="duotone" />
                            </button>
                        </div>
                    </div>
                {/if}

                <slot name="search-header" />
            </div>
        </div>
        {#if $$slots['search-actions']}
            <div class="col-12 d-flex justify-content-end pb-1">
                <slot name="search-actions" />
            </div>
        {/if}
        <slot name="multiactions" />
        {#if showDividerFilter}
            <hr class="w-100 mb-2" style="opacity:0.3;" />
        {/if}
    </div>
</div>
<div
    class="datatable datatable-default datatable-bordered datatable-head-custom datatable-scroll datatable-loaded {errorMessage ? 'datatable-error' : ''} {loading ? 'datatable-loading' : ''}"
    {id}>
    {#if errorMessage && !loading}
        <div class="datatable-table" bind:clientWidth={tableWidth} tabindex="0" aria-label="Tabella dati scorrevole orizzontalmente">
            <div class="datatable-body">
                <div class="alert alert-light-danger font-weight-bolder m-0">{errorMessage}</div>
            </div>
        </div>
    {:else}
        <div class="datatable-table" bind:clientWidth={tableWidth} tabindex="0" aria-label="Tabella dati scorrevole orizzontalmente">
            <div class="datatable-head">
                {#key rerenderKey}
                    <div class="datatable-row" style={getRowStyle(rowMinWidth)}>
                        {#if hasResponsiveDetails}
                            <span
                                class="datatable-cell datatable-toggle-detail datatable-cell-center datatable-detail-toggle-cell datatable-toggle-visible-below-{detailToggleBreakpoint}"
                                style="width: 24px;">
                                <span style="width: 24px;" />
                            </span>
                        {/if}
                        {#each columns as column, columnIndex}
                            <span
                                data-field={column.field || ''}
                                class={getCellClass(column, true)}
                                style={getColumnStyle(column, columnWidths?.[columnIndex])}
                                on:click={() => handleSort(column)}>
                                <span style={getColumnSpanStyle(column, columnWidths?.[columnIndex])}>
                                    {#if column.selector}
                                        <label class="checkbox checkbox-single mb-0" on:click|stopPropagation>
                                            <input
                                                type="checkbox"
                                                checked={dataSet.length > 0 && selectedRows.size === dataSet.length}
                                                on:change={event => toggleAll(event.currentTarget.checked)} />
                                            <span />
                                        </label>
                                    {:else}
                                        {column.title || ''}
                                        {#if column.field && column.sortable !== false && sortField === column.field}
                                            {#if sortDirection === 'desc'}
                                                <ArrowDown size={12} class="datatable-sort-icon ml-1" />
                                            {:else}
                                                <ArrowUp size={12} class="datatable-sort-icon ml-1" />
                                            {/if}
                                        {/if}
                                    {/if}
                                </span>
                            </span>
                        {/each}
                    </div>
                {/key}
            </div>
            <div class="datatable-body">
                {#if dataSet.length === 0 && !loading}
                    {#key rerenderKey}
                        <div class="datatable-row" style={getRowStyle(rowMinWidth)}>
                            <span class="datatable-cell datatable-cell-center" style="width: 100%;">
                                <span style="width: 100%; text-align: center; display: block;">Nessun dato disponibile</span>
                            </span>
                        </div>
                    {/key}
                {:else}
                    {#each dataSet as row, rowIndex (rerenderKey + '-' + rowIndex)}
                        <div
                            class="datatable-row {rowIndex % 2 === 1 ? 'datatable-row-even' : ''} {selectedRows.has(rowIndex)
                                ? 'datatable-row-active'
                                : ''}"
                            style={getRowStyle(rowMinWidth)}
                            data-row={rowIndex}
                            on:click={event => handleRowClick(event, row)}>
                            {#if hasResponsiveDetails}
                                <span
                                    class="datatable-cell datatable-toggle-detail datatable-cell-center datatable-detail-toggle-cell datatable-toggle-visible-below-{detailToggleBreakpoint}"
                                    style="width: 24px;">
                                    <span style="width: 24px; overflow: visible;">
                                        <button
                                            type="button"
                                            class="datatable-toggle-detail-button {expandedRows.has(rowIndex)
                                                ? 'datatable-toggle-detail-active'
                                                : ''}"
                                            aria-expanded={expandedRows.has(rowIndex)}
                                            aria-label={expandedRows.has(rowIndex) ? 'Nascondi dettagli' : 'Mostra dettagli'}
                                            on:click|stopPropagation={() => toggleDetails(rowIndex)}>
                                            {#if expandedRows.has(rowIndex)}
                                                <CaretDown size={16} weight="fill" />
                                            {:else}
                                                <CaretRight size={16} weight="fill" />
                                            {/if}
                                        </button>
                                    </span>
                                </span>
                            {/if}
                            {#each columns as column, columnIndex}
                                <span
                                    data-field={column.field || ''}
                                    class={getCellClass(column)}
                                    style={getColumnStyle(column, columnWidths?.[columnIndex])}>
                                    <span style={getColumnSpanStyle(column, columnWidths?.[columnIndex])}>
                                        {#if column.selector}
                                            <label class="checkbox checkbox-single mb-0" on:click|stopPropagation>
                                                <input
                                                    type="checkbox"
                                                    checked={selectedRows.has(rowIndex)}
                                                    on:change={event => toggleRow(rowIndex, event.currentTarget.checked)} />
                                                <span />
                                            </label>
                                        {:else if !isColumnHiddenAtViewport(column, viewportWidth)}
                                            {@html renderCell(column, row)}
                                        {/if}
                                    </span>
                                </span>
                            {/each}
                        </div>
                        {#if hasResponsiveDetails}
                            <div
                                class="datatable-row-detail {expandedRows.has(rowIndex) &&
                                responsiveDetailColumns.some(detailColumn =>
                                    isColumnHiddenAtViewport(detailColumn, viewportWidth)
                                )
                                    ? 'datatable-row-detail-expanded'
                                    : ''}"
                                style={getRowStyle(rowMinWidth)}
                                aria-hidden={!expandedRows.has(rowIndex) ||
                                !responsiveDetailColumns.some(detailColumn =>
                                    isColumnHiddenAtViewport(detailColumn, viewportWidth)
                                )}>
                                <div class="datatable-detail">
                                    {#each responsiveDetailColumns as detailColumn}
                                        {#if isColumnHiddenAtViewport(detailColumn, viewportWidth)}
                                            <div class="datatable-detail-row {getDetailVisibilityClass(detailColumn)}">
                                                <span class="datatable-detail-label">
                                                    {detailColumn.title || detailColumn.field || ''}
                                                </span>
                                                <span class="datatable-detail-value">
                                                    {@html renderCell(detailColumn, row)}
                                                </span>
                                            </div>
                                        {/if}
                                    {/each}
                                </div>
                            </div>
                        {/if}
                    {/each}
                {/if}
            </div>
        </div>

        <div class="datatable-pager datatable-paging-loaded">
            <ul class="datatable-pager-nav my-2 mb-sm-0">
                <li>
                    <button
                        type="button"
                        class="datatable-pager-link datatable-pager-link-first {currentPage <= 1
                            ? 'datatable-pager-link-disabled'
                            : ''}"
                        disabled={currentPage <= 1}
                        on:click={() => setPage(1)}>
                        <ChevronsLeft size={16} class="icon-xs" />
                    </button>
                </li>
                <li>
                    <button
                        type="button"
                        class="datatable-pager-link datatable-pager-link-prev {currentPage <= 1
                            ? 'datatable-pager-link-disabled'
                            : ''}"
                        disabled={currentPage <= 1}
                        on:click={() => setPage(currentPage - 1)}>
                        <ArrowLeft size={16} class="icon-xs" />
                    </button>
                </li>
                {#each getVisiblePages() as page}
                    <li>
                        <button
                            type="button"
                            class="datatable-pager-link datatable-pager-link-number {page === currentPage
                                ? 'datatable-pager-link-active'
                                : ''}"
                            on:click={() => setPage(page)}>
                            {page}
                        </button>
                    </li>
                {/each}
                <li>
                    <button
                        type="button"
                        class="datatable-pager-link datatable-pager-link-next {currentPage >= totalPages
                            ? 'datatable-pager-link-disabled'
                            : ''}"
                        disabled={currentPage >= totalPages}
                        on:click={() => setPage(currentPage + 1)}>
                        <ArrowRight size={16} class="icon-xs" />
                    </button>
                </li>
                <li>
                    <button
                        type="button"
                        class="datatable-pager-link datatable-pager-link-last {currentPage >= totalPages
                            ? 'datatable-pager-link-disabled'
                            : ''}"
                        disabled={currentPage >= totalPages}
                        on:click={() => setPage(totalPages)}>
                        <ChevronsRight size={16} class="icon-xs" />
                    </button>
                </li>
            </ul>
            <div class="datatable-pager-info my-2 mb-sm-0">
                <select
                    class="form-control form-control-sm datatable-pager-size"
                    value={pageSize}
                    on:change={event => changePageSize(event.currentTarget.value)}>
                    {#each pageSizeSelect as size}
                        <option value={size}>{size}</option>
                    {/each}
                </select>
                <span class="datatable-pager-detail ml-3">
                    Mostrati {pageStart} - {pageEnd} di {totalItems}
                </span>
            </div>
        </div>

        {#if loading}
            <div class="datatable-loader-overlay d-flex align-items-center justify-content-center" style={loaderStyle}>
                <span class="spinner spinner-{spinnerState} {spinnerMessage ? 'mr-3' : ''}" />
                {#if spinnerMessage}
                    <span class="font-weight-bolder text-muted">{spinnerMessage}</span>
                {/if}
            </div>
        {/if}
    {/if}
</div>

<style>
    .datatable-sort-icon {
        font-size: 0.6rem !important; /* theme chain outspecifies scoped rule */
        display: inline-block;
        position: relative;
        vertical-align: middle;
        line-height: 0;
    }

    .datatable.datatable-default {
        position: relative;
        width: 100%;
        max-width: 100%;
        min-width: 0;
    }

    .datatable-loader-overlay {
        position: absolute;
        top: 0;
        right: 0;
        bottom: 0;
        left: 0;
        z-index: 3;
        background-color: rgba(255, 255, 255, 0.6);
    }

    .datatable.datatable-default.datatable-loading {
        min-height: 10rem;
    }

    .datatable.datatable-default.datatable-loaded > .datatable-table,
    .datatable.datatable-default.datatable-scroll > .datatable-table {
        display: block;
        width: 100%;
        max-width: 100%;
        min-width: 0;
        overflow-x: auto !important;
        -webkit-overflow-scrolling: touch;
        overscroll-behavior-x: contain;
        padding-bottom: 0.35rem;
    }

    .datatable.datatable-default > .datatable-table:focus {
        outline: 2px solid rgba(53, 29, 194, 0.18);
        outline-offset: 2px;
    }

    .datatable.datatable-default > .datatable-table > .datatable-head,
    .datatable.datatable-default > .datatable-table > .datatable-body,
    .datatable.datatable-default > .datatable-table > .datatable-foot {
        display: block;
        visibility: visible;
        overflow: visible !important;
        width: 100%;
        min-width: 100%;
    }

    .datatable.datatable-default > .datatable-table > .datatable-head .datatable-row,
    .datatable.datatable-default > .datatable-table > .datatable-body .datatable-row {
        display: table;
        table-layout: fixed;
        width: 100%;
    }

    .datatable.datatable-default > .datatable-table > .datatable-head .datatable-row > .datatable-cell,
    .datatable.datatable-default > .datatable-table > .datatable-body .datatable-row > .datatable-cell {
        display: table-cell;
        vertical-align: middle;
        min-width: 0;
        max-width: 100%;
        box-sizing: border-box;
    }

    .datatable.datatable-default > .datatable-table > .datatable-head .datatable-row > .datatable-cell.datatable-cell-check,
    .datatable.datatable-default > .datatable-table > .datatable-body .datatable-row > .datatable-cell.datatable-cell-check {
        padding: 0.55rem !important;
    }

    .datatable.datatable-default > .datatable-table > .datatable-head .datatable-row > .datatable-cell > span,
    .datatable.datatable-default > .datatable-table > .datatable-body .datatable-row > .datatable-cell > span {
        display: block;
        min-width: 0;
        max-width: 100%;
        box-sizing: border-box;
        white-space: normal;
        overflow-wrap: anywhere;
        word-break: normal;
        overflow: visible;
        text-overflow: clip;
    }

    .datatable.datatable-default > .datatable-table > .datatable-head .datatable-row > .datatable-cell.datatable-cell-wrap > span,
    .datatable.datatable-default > .datatable-table > .datatable-body .datatable-row > .datatable-cell.datatable-cell-wrap > span {
        white-space: normal;
        overflow-wrap: anywhere;
        word-break: normal;
        overflow: visible;
        text-overflow: clip;
    }

    .datatable.datatable-default > .datatable-table > .datatable-head .datatable-row > .datatable-cell > span {
        white-space: nowrap;
        overflow-wrap: normal;
        word-break: normal;
        overflow: visible;
        text-overflow: clip;
    }

    .datatable.datatable-default > .datatable-table > .datatable-head .datatable-row > .datatable-cell.datatable-cell-nowrap > span,
    .datatable.datatable-default > .datatable-table > .datatable-body .datatable-row > .datatable-cell.datatable-cell-nowrap > span {
        white-space: nowrap;
        overflow: hidden;
        text-overflow: ellipsis;
    }

    .datatable.datatable-default > .datatable-table > .datatable-head .datatable-row > .datatable-cell.datatable-cell-actions,
    .datatable.datatable-default > .datatable-table > .datatable-body .datatable-row > .datatable-cell.datatable-cell-actions,
    .datatable.datatable-default > .datatable-table > .datatable-head .datatable-row > .datatable-cell.datatable-cell-actions > span,
    .datatable.datatable-default > .datatable-table > .datatable-body .datatable-row > .datatable-cell.datatable-cell-actions > span {
        text-align: right !important;
    }

    .datatable.datatable-default > .datatable-table > .datatable-head .datatable-row > .datatable-cell.datatable-cell-actions > span,
    .datatable.datatable-default > .datatable-table > .datatable-body .datatable-row > .datatable-cell.datatable-cell-actions > span {
        display: flex;
        width: 100% !important;
        align-items: center;
        justify-content: flex-end;
        gap: 0.35rem;
        white-space: nowrap;
        overflow: visible;
        text-overflow: clip;
    }

    .datatable.datatable-default > .datatable-table > .datatable-body .datatable-row > .datatable-cell.datatable-cell-actions .action-column,
    .datatable.datatable-default > .datatable-table > .datatable-body .datatable-row > .datatable-cell.datatable-cell-actions .info-column {
        display: flex;
        align-items: center;
        justify-content: flex-end;
        gap: 0.25rem;
        margin-left: auto;
    }

    .datatable.datatable-default > .datatable-table > .datatable-head .datatable-row > .datatable-cell.datatable-cell-overflow-visible > span,
    .datatable.datatable-default > .datatable-table > .datatable-body .datatable-row > .datatable-cell.datatable-cell-overflow-visible > span {
        overflow: visible;
        text-overflow: clip;
    }

    .datatable.datatable-default > .datatable-table > .datatable-head .datatable-row > .datatable-cell.datatable-cell-sorted > span {
        color: var(--primary, #351dc2);
    }

    .datatable-detail-toggle-cell,
    .datatable-detail-toggle-cell > span {
        padding-left: 0 !important;
        padding-right: 0 !important;
        text-align: center;
    }

    .datatable-toggle-detail-button {
        width: 1.5rem;
        height: 1.5rem;
        margin-bottom: 0;
        padding: 0;
        border: 0;
        border-radius: 50%;
        background: transparent;
        color: #351dc2;
        cursor: pointer;
        display: inline-flex;
        align-items: center;
        justify-content: center;
    }

    .datatable-toggle-detail-button:hover,
    .datatable-toggle-detail-button.datatable-toggle-detail-active {
        background-color: #f3f6f9;
    }

    .datatable-row-detail {
        display: none;
        width: 100%;
        border-bottom: 1px solid #ebedf3;
        background-color: #f9fbfd;
    }

    .datatable-row-detail.datatable-row-detail-expanded {
        display: block;
    }

    .datatable-detail {
        padding: 0.75rem 1rem 0.75rem 2.25rem;
    }

    .datatable-detail-row {
        display: none;
        align-items: flex-start;
        gap: 1rem;
        padding: 0.45rem 0;
    }

    .datatable-detail-label {
        width: 10rem;
        flex: 0 0 10rem;
        color: #b5b5c3;
        font-size: 0.85rem;
        font-weight: 600;
        letter-spacing: 0.05rem;
        text-transform: uppercase;
    }

    .datatable-detail-value {
        min-width: 0;
        flex: 1 1 auto;
        color: #3f4254;
    }

    .datatable-toggle-visible-below-sm,
    .datatable-toggle-visible-below-md,
    .datatable-toggle-visible-below-lg,
    .datatable-toggle-visible-below-xl,
    .datatable-toggle-visible-below-xxl,
    .datatable-toggle-visible-below-xxxl,
    .datatable-toggle-visible-below-xxxxl {
        display: none !important;
    }

    .datatable-pager-link {
        border: 0;
    }

    .datatable-pager-size {
        width: auto;
        min-width: 4.5rem;
        margin-right: 0.75rem;
        border: 0 !important;
        background-color: #ecf1f6 !important;
        color: #7e8299;
        font-weight: 500;
    }

    .datatable-row:hover > .datatable-cell {
        background-color: var(--bg-surface-secondary, #f3f6f9);
    }

    @media (max-width: 575.98px) {
        .datatable-cell-hidden-below-sm {
            display: none !important;
        }

        .datatable-toggle-visible-below-sm {
            display: table-cell !important;
        }

        .datatable-detail-visible-below-sm {
            display: flex !important;
        }

        .datatable.datatable-default > .datatable-table > .datatable-head .datatable-row > .datatable-cell,
        .datatable.datatable-default > .datatable-table > .datatable-body .datatable-row > .datatable-cell {
            padding-left: 0.5rem !important;
            padding-right: 0.5rem !important;
        }
    }

    @media (max-width: 767.98px) {
        .datatable-cell-hidden-below-md {
            display: none !important;
        }

        .datatable-toggle-visible-below-md {
            display: table-cell !important;
        }

        .datatable-detail-visible-below-md {
            display: flex !important;
        }
    }

    @media (max-width: 991.98px) {
        .datatable-cell-hidden-below-lg {
            display: none !important;
        }

        .datatable-toggle-visible-below-lg {
            display: table-cell !important;
        }

        .datatable-detail-visible-below-lg {
            display: flex !important;
        }
    }

    @media (max-width: 1199.98px) {
        .datatable-cell-hidden-below-xl {
            display: none !important;
        }

        .datatable-toggle-visible-below-xl {
            display: table-cell !important;
        }

        .datatable-detail-visible-below-xl {
            display: flex !important;
        }
    }

    @media (max-width: 1399.98px) {
        .datatable-cell-hidden-below-xxl {
            display: none !important;
        }

        .datatable-toggle-visible-below-xxl {
            display: table-cell !important;
        }

        .datatable-detail-visible-below-xxl {
            display: flex !important;
        }
    }

    @media (max-width: 1599.98px) {
        .datatable-cell-hidden-below-xxxl {
            display: none !important;
        }

        .datatable-toggle-visible-below-xxxl {
            display: table-cell !important;
        }

        .datatable-detail-visible-below-xxxl {
            display: flex !important;
        }
    }

    @media (max-width: 1799.98px) {
        .datatable-cell-hidden-below-xxxxl {
            display: none !important;
        }

        .datatable-toggle-visible-below-xxxxl {
            display: table-cell !important;
        }

        .datatable-detail-visible-below-xxxxl {
            display: flex !important;
        }
    }

    .datatable.datatable-default > .datatable-table {
        scrollbar-width: thin;
        scrollbar-color: var(--scrollbar-thumb, #c9ced8) var(--scrollbar-track, #f1f3f7);
    }

    .datatable.datatable-default > .datatable-table::-webkit-scrollbar {
        height: 10px;
    }

    .datatable.datatable-default > .datatable-table::-webkit-scrollbar-track {
        background: var(--scrollbar-track, #f1f3f7);
        border-radius: 999px;
    }

    .datatable.datatable-default > .datatable-table::-webkit-scrollbar-thumb {
        background: var(--scrollbar-thumb, #c9ced8);
        border: 2px solid var(--scrollbar-track, #f1f3f7);
        border-radius: 999px;
    }

    .datatable.datatable-default > .datatable-table::-webkit-scrollbar-thumb:hover,
    .datatable.datatable-default > .datatable-table:focus-within::-webkit-scrollbar-thumb {
        background: var(--scrollbar-thumb-hover, var(--primary, #351DC2));
    }
</style>
