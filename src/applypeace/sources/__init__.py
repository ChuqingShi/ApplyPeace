"""Job sources: each employer's own ATS board, so every posting is official."""

from . import ashby, greenhouse, lever, workday

LISTERS = {
    "greenhouse": greenhouse.list_jobs,
    "ashby": ashby.list_jobs,
    "lever": lever.list_jobs,
    "workday": workday.list_jobs,
}
