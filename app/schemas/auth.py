from pydantic import BaseModel, EmailStr
from typing import Optional


class RegisterRequest(BaseModel):
    name: str
    email: EmailStr
    password: str
    restaurant_name: str
    phone: Optional[str] = None


class LoginRequest(BaseModel):
    email: EmailStr
    password: str


class TokenResponse(BaseModel):
    access_token: str
    token_type: str = "bearer"
    user_id: int
    restaurant_id: Optional[int]
    name: str
    role: str
    restaurant_name: Optional[str] = None
    phone: Optional[str] = None


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

    class Config:
        from_attributes = True