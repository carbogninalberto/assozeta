// Fresh values for initialization and explicit reset; never share mutable defaults.
export const createSubscriptionListFilter = () => ({
    generalSearch: '',
    status: '',
    year: '1',
    from_age: null,
    to_age: null,
    tags: [],
    tags_and: 0,
    period_start: null,
    period_end: null,
    filter: {
        hide_associate_and_members: false,
        hide_members: false,
        certificate_missing_flag: false,
        certificate_expired_flag: false,
        subscription_not_paid_flag: false,
        sort_lastname_asc_flag: false,
        sort_lastname_desc_flag: false,
    },
});

export const createAssociatesListFilter = () => ({
    generalSearch: '',
    status: '',
    from_age: null,
    to_age: null,
    year: '1',
    period_start: null,
    period_end: null,
    filter: {
        hide_associate_and_members: false,
        expired_certificate: false,
        subscription_not_paid: false,
        sort_lastname_asc: false,
        sort_lastname_desc: false,
    },
});

export const createPersonasListFilter = () => ({
    is_tutor: '',
    generalSearch: '',
});

export const createPersonasPaymentListFilter = () => ({
    generalSearch: '',
    associateId: null,
    type: '',
    paid: '',
    subject: '',
    expense: '',
});

export const createTemplatesListFilter = () => ({
    generalSearch: '',
});

export const createCourseListFilter = () => ({
    generalSearch: '',
    status_flag: '',
    tags: [],
    tags_and: 0
});
