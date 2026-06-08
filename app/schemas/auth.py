from pydantic import BaseModel, EmailStr
from typing import Optional, List, Any
from datetime import datetime


class RegisterRequest(BaseModel):
    name: str
    email: EmailStr
    password: str
    restaurant_name: str
    phone: Optional[str] = None


class LoginRequest(BaseModel):
    email: EmailStr
    password: str


class ChangePasswordRequest(BaseModel):
    current_password: str
    new_password: str


class TokenResponse(BaseModel):
    access_token: str
    token_type: str = "bearer"
    user_id: int
    restaurant_id: Optional[int]
    name: str
    role: str
    restaurant_name: Optional[str] = None
    phone: Optional[str] = None
    trial_ends_at: Optional[datetime] = None
    days_left: Optional[int] = None
    enabled_modules: Optional[dict] = None


class UserOut(BaseModel):
    id: int
    name: str
    email: str
    role: str
    phone: Optional[str]
    restaurant_id: Optional[int]

    class Config:
        from_attributes = True


class UpdateProfileRequest(BaseModel):
    name: Optional[str] = None
    phone: Optional[str] = None
    restaurant_name: Optional[str] = None
    address: Optional[str] = None
    city: Optional[str] = None
    gstin: Optional[str] = None
    fssai: Optional[str] = None
    gst_rate: Optional[float] = None
    # Integrations
    zomato_enabled: Optional[bool] = None
    zomato_secret: Optional[str] = None
    zomato_restaurant_id: Optional[str] = None
    swiggy_enabled: Optional[bool] = None
    swiggy_secret: Optional[str] = None
    swiggy_restaurant_id: Optional[str] = None
    razorpay_enabled: Optional[bool] = None
    razorpay_key_id: Optional[str] = None
    razorpay_key_secret: Optional[str] = None
    # Pine Labs
    pinelabs_enabled: Optional[bool] = None
    pinelabs_merchant_id: Optional[str] = None
    pinelabs_terminal_id: Optional[str] = None
    pinelabs_security_token: Optional[str] = None
    table_count: Optional[int] = None
    table_sections: Optional[List[Any]] = None
    round_off: Optional[bool] = None
    loyalty_enabled: Optional[bool] = None
    show_gst_breakup: Optional[bool] = None


class ProfileOut(BaseModel):
    name: str
    email: str
    phone: Optional[str]
    restaurant_name: str
    address: Optional[str]
    city: Optional[str]
    gstin: Optional[str]
    fssai: Optional[str]
    gst_rate: float
    restaurant_id: Optional[int] = None
    # Integrations
    zomato_enabled: Optional[bool] = False
    zomato_restaurant_id: Optional[str] = None
    swiggy_enabled: Optional[bool] = False
    swiggy_restaurant_id: Optional[str] = None
    razorpay_enabled: Optional[bool] = False
    razorpay_key_id: Optional[str] = None
    # Pine Labs
    pinelabs_enabled: Optional[bool] = False
    pinelabs_merchant_id: Optional[str] = None
    pinelabs_terminal_id: Optional[str] = None
    table_count: int = 10
    table_sections: Optional[List[Any]] = None
    enabled_modules: Optional[dict] = None
    plan: Optional[str] = None
    trial_ends_at: Optional[datetime] = None
    days_left: Optional[int] = None
    is_active: Optional[bool] = None
    round_off: bool = False
    loyalty_enabled: bool = True
    show_gst_breakup: bool = True

    class Config:
        from_attributes = True