from __future__ import annotations

import os
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from .api.routes import router
from .brand import PRODUCT_TITLE


app = FastAPI(
    root_path=os.getenv('SHUCE_ROOT_PATH', ''),
    title=f"{PRODUCT_TITLE} API",
    version="1.0.0",
    description="面向业务人员的自然语言问数、经营复盘与报告服务",
)
app.add_middleware(
    CORSMiddleware,
    allow_origins=[
        "http://localhost:5173",
        "http://127.0.0.1:5173",
        "http://localhost:5174",
        "http://127.0.0.1:5174",
    ],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)
app.include_router(router)
